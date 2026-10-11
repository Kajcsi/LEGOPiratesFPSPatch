#!/usr/bin/env python3
"""LEGO Pirates of the Caribbean: The Video Game - high frame rate fix.

Fixes the places in LEGOPirates.exe (v1.1.0.0) where game physics depend on
the frame rate, so the game behaves the same at 144, 240 or 1000+ fps as it
does at 60 fps. There is no frame cap: the game keeps rendering at whatever
rate your monitor / VSync allows.

Usage:
    python patch_fps.py [path\\to\\LEGOPirates.exe]           patch
    python patch_fps.py --restore [path\\to\\LEGOPirates.exe] undo
    python patch_fps.py --check [path\\to\\LEGOPirates.exe]   report only
    python patch_fps.py --laa [path\\to\\LEGOPirates.exe]     patch + Large Address Aware

The original exe is copied to LEGOPirates.exe.prefps before anything is
written. An exe patched by a different version of this script is rebuilt from
that backup. See README.md and TECHNICAL.md for what is changed and why.
"""
import argparse
import hashlib
import math
import os
import shutil
import struct
import sys

VERSION = "1.0.0"
SECTION_NAME = b".fpsfix\0"    # new code + constants (read/execute)
DATA_NAME = b".fpsdat\0"       # one float of state (read/write, zero-initialised)
CAVE_SIZE = 0x200
DATA_SIZE = 0x10
BACKUP_SUFFIX = ".prefps"
IMAGE_BASE = 0x400000
SIG_PREFIX = b"LEGOPiratesFPSPatch "
SIG = SIG_PREFIX + VERSION.encode()
SIG_OFF = 0x100

# Game globals the new code reads.
G_DT = 0xF76670        # float: this frame's game time step (seconds, after time scale)
G_1_30 = 0xA9C944      # float 1/30: the physics step's upper clamp
G_1MS = 0xA77358       # float 0.001: the physics step's lower clamp

# Known v1.1.0.0 executables this patcher was built and tested against.
KNOWN = {
    "39de791e932a45af6dc5c002d701cd6cdac821b92f7b84166599065348dedc69": "v1.1.0.0 (GOG)",
    "c9a1ffc7de654e0dfc70304a56d6cc8a063eb191a3e96b96c0534da5bcb3c9c5": "v1.1.0.0 (GOG) + Large Address Aware",
}

# Original bytes at every patch site (virtual address -> bytes). The patcher
# refuses to touch an exe where any of these differ.
SITES = {
    # 1. Push block: block velocity x/z *= 0.9 every frame after it is released.
    #   796e35  fld qword ptr [0xa72790]        ; 0.9
    0x796E35: bytes.fromhex("dd059027a700"),
    # 2. Push-engage timer: += 2/30 every frame (when dt <= 1/30).
    #   82bfe4  fld st0 / fld1 / fdivrp / fdiv [30.0] / fadd st,st / fmulp / jmp 82bff8
    0x82BFE4: bytes.fromhex("d9c0d9e8def1dc35d83ca700dcc0dec9eb02"),
    # 3. Vehicle (cLegoVehicle): angular velocity *= 0.9 every frame while steering.
    #   8345d0  fld qword ptr [0xa72790]        ; 0.9
    0x8345D0: bytes.fromhex("dd059027a700"),
    # 4. Vehicle (cLegoVehicle): fields +0x14c/+0x154 *= 0.8 every frame when free.
    #   85048a  fld qword ptr [0xa72c60]        ; 0.8
    0x85048A: bytes.fromhex("dd05602ca700"),
    # 5. Rigid-body physics step (9a1340): dt is clamped to [0.001, 1/30] and the
    #    world is stepped once per frame, so above 1000 fps it runs faster than
    #    real time. The whole clamp block is replaced by an accumulator.
    #   9a1387  fld [1/30] / fld [ebp+8] / fcom ... (clamp dt to [0.001, 1/30])
    0x9A1387: bytes.fromhex("d90544c9a900d94508d8d1dfe0f6c4417507ddd8d95d08eb1addd9d9055873a700"
                            "d8d1dfe0ddd9f6c4417505d95d08eb02ddd8"),
    #    The three other places that clamp the same step size the same way
    #    (1/dt for kinematic bodies, ragdolls and character push-out) must
    #    see the same step, so they add the accumulated time too.
    #   98f1d7  fld dword ptr [0xa9c944]        ; character push-out (98f1a0)
    0x98F1D7: bytes.fromhex("d90544c9a900"),
    #   56296a  fld dword ptr [0xa9c944]        ; ragdoll / bones (562950)
    0x56296A: bytes.fromhex("d90544c9a900"),
    #   53f8a0  fld dword ptr [0xa9c944]        ; kinematic bodies (53f8a0, clamps *arg)
    0x53F8A0: bytes.fromhex("d90544c9a900"),
    # 6. Path follow: a fixed 0.0025 is added to the height offset every frame,
    #    and smoothing pulls it back, so the resting offset grows with fps.
    #   786b07  fmul qword ptr [0xbf0b10]       ; 0.0025
    0x786B07: bytes.fromhex("dc0d100bbf00"),
    # 7. Character capsule height: smoothed height += (target - smoothed) * 0.5
    #    every frame.
    #   99ef6b  fmul dword ptr [0xe2791c]       ; 0.5
    0x99EF6B: bytes.fromhex("d80d1c79e200"),
    # 8. Critters (three update functions): the move speed is used as a distance
    #    per frame, so they walk faster the higher the fps.
    #   44cfa2  fstp dword ptr [esp+0x18] / push ecx
    0x44CFA2: bytes.fromhex("d95c241851"),
    #   433b57  fstp dword ptr [esp+0x18] / mov eax,[esp+0x1c]
    0x433B57: bytes.fromhex("d95c24188b44241c"),
    #   434049  fstp dword ptr [esp+0x18] / mov eax,[esp+0x1c]
    0x434049: bytes.fromhex("d95c24188b44241c"),
}

# Context bytes that must also match (not modified), so a different build
# with the same short patterns at the same address is never patched.
CONTEXT = {
    0x796E31: bytes.fromhex("d9442410"),              # fld  dword [esp+0x10]
    0x796E3B: bytes.fromhex("dcc9d9c9d95c2410"),      # fmul st1,st / fxch / fstp [esp+0x10]
    0x796E6D: bytes.fromhex("d86508"),                # fsub dword [ebp+8]         (dt)
    0x82BFCD: bytes.fromhex("d90544c9a900d9057066f700"),  # fld [1/30] / fld [dt]
    0x82BFF6: bytes.fromhex("dcc0d8863c080000d99e3c080000"),  # fadd / fadd [esi+0x83c] / fstp
    0x834368: bytes.fromhex("8974243c"),              # mov  [esp+0x3c], esi       (this)
    0x8345C3: bytes.fromhex("8b44243c8b8030020000d94030"),  # mov eax,[esp+0x3c] / [eax+0x230] / fld [eax+0x30]
    0x8345D6: bytes.fromhex("dcc9d9c9d95c2440"),      # fmul st1,st / fxch / fstp [esp+0x40]
    0x8503C4: bytes.fromhex("d99f10010000"),          # fstp [edi+0x110]           (dt)
    0x850484: bytes.fromhex("d9874c010000"),          # fld  dword [edi+0x14c]
    0x850490: bytes.fromhex("dcc9d9c9d99f4c010000d88f54010000d99f54010000"),
    0x9A1361: bytes.fromhex("d84d08538b5d0c5657d95d088bf9"),  # fmul [ebp+8] / push ebx / ... / mov edi,ecx
    0x9A137E: bytes.fromhex("d9052881e200d95d08"),    # fld [1/30] / fstp [ebp+8]  (fixed-step mode)
    0x9A13BA: bytes.fromhex("8b773485f6"),            # mov esi,[edi+0x34] / test esi,esi
    0x9A1497: bytes.fromhex("8b4c244c5f5e5b33cc"),    # epilogue: mov ecx,[esp+0x4c] / pop edi,esi,ebx
    0x98F1C5: bytes.fromhex("8b450c535657"),          # mov eax,[ebp+0xc] / push ebx,esi,edi
    0x98F1DD: bytes.fromhex("d94508d8d1dfe0f6c441"),  # fld [ebp+8] / fcom st1 / fnstsw / test ah,41
    0x562970: bytes.fromhex("8b450cd9450853d8d1"),    # mov eax,[ebp+0xc] / fld [ebp+8] / push ebx / fcom
    0x53F8A6: bytes.fromhex("8b4c2404d811dfe0"),      # mov ecx,[esp+4] / fcom [ecx] / fnstsw
    0x786B03: bytes.fromhex("d9442428"),              # fld  dword [esp+0x28]
    0x786B0D: bytes.fromhex("d84628d95e28"),          # fadd [esi+0x28] / fstp [esi+0x28]
    0x99EF57: bytes.fromhex("d946745fd8a6f4000000b001d95c2408d9442408"),
    0x99EF71: bytes.fromhex("d886f4000000d99ef4000000"),  # fadd / fstp [esi+0xf4]
    0x44CF9C: bytes.fromhex("d9870c010000"),          # fld  dword [edi+0x10c]     (max speed)
    0x44CFA7: bytes.fromhex("d902d944241c"),          # fld [edx] / fld [esp+0x1c]
    0x433B51: bytes.fromhex("d9870c010000"),
    0x433B5F: bytes.fromhex("d94620"),                # fld  dword [esi+0x20]
    0x434043: bytes.fromhex("d9870c010000"),
    0x434051: bytes.fromhex("d94620"),
}


def log(msg):
    print(msg, flush=True)


class PE:
    def __init__(self, data):
        self.d = bytearray(data)
        d = self.d
        if d[:2] != b"MZ":
            raise ValueError("not an EXE (no MZ header)")
        self.pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[self.pe:self.pe + 4] != b"PE\0\0":
            raise ValueError("not a PE file")
        self.nsec_off = self.pe + 6
        self.chars_off = self.pe + 22
        self.opt = self.pe + 24
        if struct.unpack_from("<H", d, self.opt)[0] != 0x10B:
            raise ValueError("not a 32-bit executable")
        self.optsize = struct.unpack_from("<H", d, self.pe + 20)[0]
        self.image_base = struct.unpack_from("<I", d, self.opt + 28)[0]
        self.sect_align = struct.unpack_from("<I", d, self.opt + 32)[0]
        self.file_align = struct.unpack_from("<I", d, self.opt + 36)[0]
        self.size_of_headers = struct.unpack_from("<I", d, self.opt + 60)[0]
        self.dll_chars = struct.unpack_from("<H", d, self.opt + 70)[0]
        self.sec_table = self.opt + self.optsize
        self.sections = []
        for i in range(struct.unpack_from("<H", d, self.nsec_off)[0]):
            o = self.sec_table + i * 40
            name = bytes(d[o:o + 8])
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((name, va, vsize, rptr, rsize))

    def off(self, va):
        rva = va - self.image_base
        for _, sva, vsize, rptr, rsize in self.sections:
            if sva <= rva < sva + min(vsize, rsize):
                return rptr + rva - sva
        raise ValueError("address %#x is not in the file" % va)

    def read(self, va, n):
        o = self.off(va)
        return bytes(self.d[o:o + n])

    def write(self, va, b):
        o = self.off(va)
        self.d[o:o + len(b)] = b

    def section(self, name):
        for s in self.sections:
            if s[0] == name:
                return s
        return None


def align(x, a):
    return (x + a - 1) // a * a


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def build_cave(sva, dva):
    """Code for the new section, assembled by hand (x87, 32-bit).

    pow(c, 60*dt) is evaluated as 2^(dt * 60*log2(c)), so at 60 fps the factor
    is exactly the original per-frame constant and at any other rate the decay
    per second is identical.

    sva is the address of the code section, dva the address of the data
    section. Returns the section bytes and a dict of entry points.
    """
    K09 = sva + 0x00    # float 60*log2(0.9)
    K08 = sva + 0x04    # float 60*log2(0.8)
    C2_30 = sva + 0x08  # float 2/30
    KM60 = sva + 0x0C   # float 60*log2(0.5) = -60
    EXP2 = sva + 0x10
    K60 = sva + 0x24    # float 60
    K015 = sva + 0x28   # float 0.15 = 0.0025 * 60
    ACC = dva + 0x00    # float: game time not yet given to the physics step
    cave = bytearray(CAVE_SIZE)
    # The game compares against the float constants 0.9f / 0.8f widened to double.
    struct.pack_into("<f", cave, 0x00, 60.0 * math.log2(f32(0.9)))
    struct.pack_into("<f", cave, 0x04, 60.0 * math.log2(f32(0.8)))
    struct.pack_into("<f", cave, 0x08, 2.0 / 30.0)
    struct.pack_into("<f", cave, 0x0C, -60.0)
    struct.pack_into("<f", cave, 0x24, 60.0)
    struct.pack_into("<f", cave, 0x28, 0.15)

    def u32(x):
        return struct.pack("<I", x)

    def put(off, code):
        assert not any(cave[off:off + len(code)]), "cave overlap at %#x" % off
        cave[off:off + len(code)] = code
        return sva + off

    def rel(at, target):
        return struct.pack("<i", target - (at + 4))

    # exp2: st0 = y  ->  st0 = 2^y   (valid for any finite y, any rounding mode)
    put(0x10, bytes.fromhex(
        "d9c0"      # fld   st(0)            y, y
        "d9fc"      # frndint                n, y
        "dce9"      # fsub  st(1), st(0)     n, f=y-n
        "d9c9"      # fxch  st(1)            f, n
        "d9f0"      # f2xm1                  2^f-1, n
        "d9e8"      # fld1
        "dec1"      # faddp st(1), st(0)     2^f, n
        "d9fd"      # fscale                 2^y, n
        "ddd9"      # fstp  st(1)            2^y
        "c3"        # ret
    ))

    def jmp_exp2(off, c):
        return c + b"\xe9" + rel(sva + off + len(c) + 1, EXP2)

    def call_exp2(off, c):
        return c + b"\xe8" + rel(sva + off + len(c) + 1, EXP2)

    e = {}
    # 1. 796b20 push block: dt is the function's stack argument [ebp+8].
    c = bytes.fromhex("d94508")                                 # fld  dword [ebp+8]
    c += b"\xd8\x0d" + u32(K09)                                 # fmul dword [K09]
    e["push"] = put(0x30, jmp_exp2(0x30, c))                    # jmp  exp2

    # 3. 834350 vehicle: this = [esp+0x3c] in the caller (+4 for our return
    #    address); dt is stored at this+0x110 by 850360. edx is dead here.
    c = bytes.fromhex("8b542440")                               # mov  edx, [esp+0x40]
    c += bytes.fromhex("d98210010000")                          # fld  dword [edx+0x110]
    c += b"\xd8\x0d" + u32(K09)                                 # fmul dword [K09]
    e["veh_turn"] = put(0x40, jmp_exp2(0x40, c))

    # 4. 850360 vehicle: edi = this, dt at this+0x110.
    c = bytes.fromhex("d98710010000")                           # fld  dword [edi+0x110]
    c += b"\xd8\x0d" + u32(K08)                                 # fmul dword [K08]
    e["veh_free"] = put(0x60, jmp_exp2(0x60, c))

    # 6. 786b07 path follow: offset += x * 0.0025 per frame  ->  x * 0.15 * dt
    c = b"\xd8\x0d" + u32(G_DT)                                 # fmul dword [dt]
    c += b"\xd8\x0d" + u32(K015)                                # fmul dword [K015]
    c += b"\xc3"                                                # ret
    e["path"] = put(0x80, c)

    # 7. 99ef6b capsule height: st0 = target - smoothed.
    #    st0 *= 1 - 0.5^(60*dt)   (= 0.5 at 60 fps)
    c = b"\xd9\x05" + u32(G_DT)                                 # fld  dword [dt]
    c += b"\xd8\x0d" + u32(KM60)                                # fmul dword [KM60]   -60dt, d
    c = call_exp2(0x90, c)                                      # call exp2           0.5^(60dt), d
    c += bytes.fromhex("d9e8")                                  # fld1                1, p, d
    c += bytes.fromhex("dee1")                                  # fsubrp st(1), st    1-p, d
    c += bytes.fromhex("dec9")                                  # fmulp  st(1), st    d*(1-p)
    c += b"\xc3"                                                # ret
    e["capsule"] = put(0x90, c)

    # 8. Critters: st0 = move speed (a distance per frame at 60 fps).
    #    Store speed * 60 * dt instead, then redo the instruction we replaced.
    #    44cfa2  fstp [esp+0x18] / push ecx
    c = b"\xd8\x0d" + u32(G_DT)                                 # fmul dword [dt]
    c += b"\xd8\x0d" + u32(K60)                                 # fmul dword [K60]
    c += bytes.fromhex("d95c241c")                              # fstp dword [esp+0x1c]
    c += bytes.fromhex("51")                                    # push ecx
    c += bytes.fromhex("874c2404")                              # xchg ecx, [esp+4]   ecx = return address
    c += bytes.fromhex("870c24")                                # xchg ecx, [esp]     restore ecx, [esp] = return
    c += b"\xc3"                                                # ret                 leaves ecx pushed
    e["critter_a"] = put(0xB0, c)
    #    433b57 / 434049  fstp [esp+0x18] / mov eax,[esp+0x1c]
    c = b"\xd8\x0d" + u32(G_DT)                                 # fmul dword [dt]
    c += b"\xd8\x0d" + u32(K60)                                 # fmul dword [K60]
    c += bytes.fromhex("d95c241c")                              # fstp dword [esp+0x1c]
    c += bytes.fromhex("8b442420")                              # mov  eax, [esp+0x20]
    c += b"\xc3"                                                # ret
    e["critter_b"] = put(0xD0, c)

    put(SIG_OFF, SIG)

    # 5. Physics step accumulator (9a1340). Frame time is collected in ACC until
    #    it reaches the engine's minimum step of 1 ms; then the world is stepped
    #    once by min(ACC + dt, 1/30) and ACC is cleared. Returns eax = 0 to skip
    #    this frame's step. Below 1000 fps every frame steps exactly as before.
    c = bytes.fromhex("d94508")                                 # fld   dword [ebp+8]       dt
    c += b"\xd8\x05" + u32(ACC)                                 # fadd  dword [ACC]         a = acc + dt
    c += b"\xd9\x05" + u32(G_1MS)                               # fld   dword [0.001]       0.001, a
    c += bytes.fromhex("dff1")                                  # fcomip st, st(1)          a
    step = b"\xd9\x05" + u32(G_1_30)                            # fld   dword [1/30]        1/30, a
    step += bytes.fromhex("dbf1")                               # fcomi st, st(1)
    step += bytes.fromhex("dbc1")                               # fcmovnb st, st(1)         min(1/30, a), a
    step += bytes.fromhex("d95d08")                             # fstp  dword [ebp+8]       step size
    step += bytes.fromhex("ddd8")                               # fstp  st(0)
    step += b"\xc7\x05" + u32(ACC) + u32(0)                     # mov   dword [ACC], 0
    step += bytes.fromhex("b801000000")                         # mov   eax, 1
    step += b"\xc3"                                             # ret
    skip = b"\xd9\x1d" + u32(ACC)                               # .skip: fstp dword [ACC]   (keep a)
    skip += bytes.fromhex("33c0")                               #        xor  eax, eax
    skip += b"\xc3"                                             #        ret
    c += b"\x77" + bytes([len(step)])                           # ja    .skip               (0.001 > a)
    e["step"] = put(0x120, c + step + skip)

    # 5b. 98f1d7 / 56296a: replaces "fld [1/30]" right before the original
    #     clamp of [ebp+8]. The clamp then gives the same step as 9a1340.
    c = bytes.fromhex("d94508")                                 # fld   dword [ebp+8]
    c += b"\xd8\x05" + u32(ACC)                                 # fadd  dword [ACC]
    c += bytes.fromhex("d95d08")                                # fstp  dword [ebp+8]
    c += b"\xd9\x05" + u32(G_1_30)                              # fld   dword [1/30]        (replaced instruction)
    c += b"\xc3"                                                # ret
    e["acc_ebp"] = put(0x160, c)

    # 5c. 53f8a0(float *dt): same, for the pointer argument.
    c = bytes.fromhex("8b4c2408")                               # mov   ecx, [esp+8]        dt pointer
    c += bytes.fromhex("d901")                                  # fld   dword [ecx]
    c += b"\xd8\x05" + u32(ACC)                                 # fadd  dword [ACC]
    c += bytes.fromhex("d919")                                  # fstp  dword [ecx]
    c += b"\xd9\x05" + u32(G_1_30)                              # fld   dword [1/30]        (replaced instruction)
    c += b"\xc3"                                                # ret
    e["acc_ptr"] = put(0x180, c)

    e["c2_30"] = C2_30
    return bytes(cave), e


def call_at(site, target, length):
    """call target, padded with nops to the length of the replaced bytes"""
    return b"\xe8" + struct.pack("<i", target - (site + 5)) + b"\x90" * (length - 5)


def push_timer(c2_30):
    # st0 = dt on entry (dt <= 1/30 branch). Original: += 2/30 per frame.
    # New: += min(4*dt, 2/30) -> same as before at <= 60 fps, 4 per second above.
    b = bytes.fromhex("d8c0d8c0")                               # fadd st,st ; fadd st,st  -> 4dt
    b += b"\xd9\x05" + struct.pack("<I", c2_30)                 # fld  dword [2/30]
    b += bytes.fromhex("dbf1")                                  # fcomi   st, st(1)
    b += bytes.fromhex("dbc1")                                  # fcmovnb st, st(1)   (2/30 >= 4dt -> 4dt)
    b += bytes.fromhex("ddd9")                                  # fstp st(1)
    b += bytes.fromhex("eb02")                                  # jmp 82bff8 (unchanged target)
    assert len(b) == 18
    return b


def physics_step_site(step):
    # 9a1387..9a13b9 (51 bytes): call STEP / test eax,eax / je epilogue / jmp 9a13ba
    site = 0x9A1387
    b = b"\xe8" + struct.pack("<i", step - (site + 5))         # call STEP
    b += bytes.fromhex("85c0")                                  # test eax, eax
    b += b"\x0f\x84" + struct.pack("<i", 0x9A1497 - (site + 13))  # je 9a1497 (no step this frame)
    b += b"\xeb" + bytes([0x9A13BA - (site + 15)])              # jmp 9a13ba (step)
    b += b"\x90" * (len(SITES[site]) - len(b))
    return b


def read_sig(pe):
    """The patch signature stored in an already patched exe, or None."""
    s = pe.section(SECTION_NAME)
    if not s:
        return None
    try:
        raw = pe.read(pe.image_base + s[1] + SIG_OFF, 32)
    except ValueError:
        return b"?"
    return raw.split(b"\0")[0]


def check(pe):
    """Return (state, problems). state: 'original', 'patched' or 'unknown'."""
    if pe.section(SECTION_NAME):
        return "patched", []
    problems = []
    if pe.image_base != IMAGE_BASE:
        problems.append("image base is %#x, expected %#x" % (pe.image_base, IMAGE_BASE))
    if pe.dll_chars & 0x40:
        problems.append("exe is relocatable (ASLR); expected a fixed-base v1.1.0.0 exe")
    for table in (SITES, CONTEXT):
        for va, want in table.items():
            try:
                got = pe.read(va, len(want))
            except ValueError as e:
                problems.append(str(e))
                continue
            if got != want:
                problems.append("bytes at %#x are %s, expected %s" % (va, got.hex(), want.hex()))
    return ("original" if not problems else "unknown"), problems


def patch(pe, file_len):
    last_raw_end = max(s[3] + s[4] for s in pe.sections)
    if file_len != last_raw_end:
        raise ValueError("exe has extra data after its last section; refusing to append")
    nsec = len(pe.sections)
    hdr = pe.sec_table + nsec * 40
    if hdr + 80 > pe.size_of_headers or any(pe.d[hdr:hdr + 80]):
        raise ValueError("no room for two new section headers")
    top = max(s[1] + s[2] for s in pe.sections)
    code_rva = align(top, pe.sect_align)
    data_rva = align(code_rva + CAVE_SIZE, pe.sect_align)
    new_raw = align(file_len, pe.file_align)
    raw_size = align(CAVE_SIZE, pe.file_align)
    sva = pe.image_base + code_rva

    cave, e = build_cave(sva, pe.image_base + data_rva)
    cave = cave.ljust(raw_size, b"\0")

    # section headers
    struct.pack_into("<8sIIIIIIHHI", pe.d, hdr, SECTION_NAME, CAVE_SIZE, code_rva, raw_size,
                     new_raw, 0, 0, 0, 0, 0x60000020)          # code | execute | read
    struct.pack_into("<8sIIIIIIHHI", pe.d, hdr + 40, DATA_NAME, DATA_SIZE, data_rva, 0,
                     0, 0, 0, 0, 0, 0xC0000080)                # uninitialised data | read | write
    struct.pack_into("<H", pe.d, pe.nsec_off, nsec + 2)
    struct.pack_into("<I", pe.d, pe.opt + 56, align(data_rva + DATA_SIZE, pe.sect_align))  # SizeOfImage
    for field, add in ((4, raw_size), (12, align(DATA_SIZE, pe.file_align))):  # SizeOfCode, SizeOfUninitializedData
        v = struct.unpack_from("<I", pe.d, pe.opt + field)[0]
        struct.pack_into("<I", pe.d, pe.opt + field, v + add)
    pe.d.extend(b"\0" * (new_raw - file_len))
    pe.d.extend(cave)
    pe.sections.append((SECTION_NAME, code_rva, CAVE_SIZE, new_raw, raw_size))
    pe.sections.append((DATA_NAME, data_rva, DATA_SIZE, 0, 0))

    targets = {
        0x796E35: e["push"],
        0x8345D0: e["veh_turn"],
        0x85048A: e["veh_free"],
        0x98F1D7: e["acc_ebp"],
        0x56296A: e["acc_ebp"],
        0x53F8A0: e["acc_ptr"],
        0x786B07: e["path"],
        0x99EF6B: e["capsule"],
        0x44CFA2: e["critter_a"],
        0x433B57: e["critter_b"],
        0x434049: e["critter_b"],
    }
    for site, target in targets.items():
        pe.write(site, call_at(site, target, len(SITES[site])))
    pe.write(0x82BFE4, push_timer(e["c2_30"]))
    pe.write(0x9A1387, physics_step_site(e["step"]))
    assert set(targets) | {0x82BFE4, 0x9A1387} == set(SITES)

    # Keep the PE checksum valid if the original had one (v1.1.0.0 has 0).
    if struct.unpack_from("<I", pe.d, pe.opt + 64)[0]:
        struct.pack_into("<I", pe.d, pe.opt + 64, pe_checksum(pe.d, pe.opt + 64))


def pe_checksum(d, cks_off):
    s = 0
    n = len(d)
    buf = bytes(d) + (b"\0" if n % 2 else b"")
    for i in range(0, len(buf), 2):
        if i == cks_off or i == cks_off + 2:
            continue
        s += buf[i] | (buf[i + 1] << 8)
        s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return (s + n) & 0xFFFFFFFF


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def find_exe(arg):
    if arg:
        p = arg
        if os.path.isdir(p):
            p = os.path.join(p, "LEGOPirates.exe")
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(os.getcwd(), "LEGOPirates.exe"), os.path.join(here, "LEGOPirates.exe")):
        if os.path.isfile(cand):
            return cand
    return os.path.join(os.getcwd(), "LEGOPirates.exe")


def main():
    ap = argparse.ArgumentParser(description="LEGO Pirates of the Caribbean high frame rate fix " + VERSION)
    ap.add_argument("exe", nargs="?", help="path to LEGOPirates.exe or the game folder")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--restore", action="store_true", help="put the backed-up original exe back")
    g.add_argument("--check", action="store_true", help="only report whether the exe can be patched")
    ap.add_argument("--laa", action="store_true",
                    help="also set the Large Address Aware flag (lets the 32-bit game use 4 GB of RAM)")
    a = ap.parse_args()

    exe = find_exe(a.exe)
    backup = exe + BACKUP_SUFFIX
    if not os.path.isfile(exe):
        log("LEGOPirates.exe not found: %s" % exe)
        log("Run this from the game folder or pass the path to the exe.")
        return 2

    if a.restore:
        if not os.path.isfile(backup):
            log("No backup found (%s). Nothing to restore." % backup)
            return 1
        shutil.copy2(backup, exe)
        log("Restored %s from %s" % (exe, backup))
        return 0

    data = open(exe, "rb").read()
    h = sha256(data)
    log("exe:     %s" % exe)
    log("sha256:  %s  %s" % (h, KNOWN.get(h, "")))
    pe = PE(data)
    state, problems = check(pe)
    keep_laa = False
    if state == "patched":
        sig = read_sig(pe)
        if sig == SIG:
            log("This exe is already patched (version %s). Use --restore to undo." % VERSION)
            return 0
        old = sig[len(SIG_PREFIX):].decode("ascii", "replace") if sig and sig.startswith(SIG_PREFIX) else "unknown"
        log("This exe has a different version of this patch (%s)." % old)
        if not os.path.isfile(backup):
            log("The backup %s is missing, so it can't be updated." % backup)
            log("Put the original exe back and run this again.")
            return 1
        orig = open(backup, "rb").read()
        pe_orig = PE(orig)
        if check(pe_orig)[0] != "original":
            log("The backup %s is not an unpatched v1.1.0.0 exe, so nothing was changed." % backup)
            return 1
        if a.check:
            log("OK: run without --check to update it to %s (rebuilt from the backup)." % VERSION)
            return 0
        log("update:  rebuilding from the backup %s" % backup)
        keep_laa = bool(struct.unpack_from("<H", pe.d, pe.chars_off)[0] & 0x20)
        data, pe = orig, pe_orig
    elif state != "original":
        log("This exe does not match v1.1.0.0, so it was NOT changed:")
        for p in problems[:10]:
            log("  - " + p)
        log("The patch needs the unencrypted v1.1.0.0 LEGOPirates.exe (see README).")
        return 1
    elif a.check:
        log("OK: exe matches v1.1.0.0 and can be patched.")
        return 0

    patch(pe, len(data))
    if a.laa or keep_laa:
        ch = struct.unpack_from("<H", pe.d, pe.chars_off)[0]
        struct.pack_into("<H", pe.d, pe.chars_off, ch | 0x20)

    # verify what we wrote
    out = bytes(pe.d)
    pe2 = PE(out)
    if read_sig(pe2) != SIG or not pe2.section(DATA_NAME):
        log("Internal error: patched image failed verification; nothing written.")
        return 3

    if not os.path.isfile(backup):
        shutil.copy2(exe, backup)
        log("backup:  %s" % backup)
    else:
        log("backup:  %s (already exists, kept)" % backup)
    tmp = exe + ".tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, exe)
    log("patched: sha256 %s" % sha256(out))
    log("Done. The game now runs its per-frame physics at any frame rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
