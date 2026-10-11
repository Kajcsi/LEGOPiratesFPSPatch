# Technical notes

This document explains what the patcher changes in `LEGOPirates.exe` (v1.1.0.0,
32-bit, image base `0x400000`, no ASLR) and why. All addresses are virtual
addresses in that exe. Functions are named by their start address, the way
Ghidra names them (`FUN_009a1340` is written `9a1340`).

## Contents

- [How the game measures time](#how-the-game-measures-time)
- [How the audit was done](#how-the-audit-was-done)
- [The eight bugs](#the-eight-bugs)
- [The new code](#the-new-code)
- [The physics step accumulator](#the-physics-step-accumulator)
- [Jumping](#jumping)
- [Checked and correct already](#checked-and-correct-already)
- [Frame-rate dependent but visual only (not patched)](#frame-rate-dependent-but-visual-only-not-patched)
- [Testing](#testing)
- [Exe layout after patching](#exe-layout-after-patching)

## How the game measures time

- The frame time comes from `QueryPerformanceCounter`. It stays precise even
  at several thousand fps.
- After the game's time scale is applied, the frame time is stored as a float
  at `0xF76670`. The rest of this document calls it `dt`. Most game code reads
  it directly, or receives it as a function argument.
- Constants used below:

  | Address | Value |
  |---|---|
  | `0xA9C944` | `1/30` |
  | `0xA77358` | `0.001` |
  | `0xA72790` | `0.9` (double) |
  | `0xA72C60` | `0.8` (double) |
  | `0xBF0B10` | `0.0025` (double) |
  | `0xE2791C` | `0.5` |

- The game was built for 30 and 60 fps. Most systems already scale by `dt`.
  The bugs below are the places that don't.

## How the audit was done

The exe was decompiled with Ghidra (through [REA](https://github.com/morluto/rea))
and the whole `.text` section was disassembled. These scans were then run:

- **Per-frame multipliers.** Every `fmul` by a constant between 0 and 1 whose
  result is stored back into the same object field (`x = x * c`). Both the C
  output and the raw assembly were searched. The assembly search catches the
  `fld c / fmul st(1),st / fxch / fstp` form that the C output hides.
- **Per-frame additions.** Every constant added to a stored field each frame,
  without a `dt` factor (`x = x + c`).
- **Hard-coded time steps.** All 39 functions that use `1/60` or `1/30` as a
  time step.
- **Frame counters.** All 106 uses of the engine's integer frame counter
  (+1 per rendered frame), looking for "wait N frames" timers.
- **`dt` clamps.** Every place that clamps `dt` to a minimum, especially
  the `0.001` minimum used by the physics engine.
- **Physics engine.** The rigid-body step, integrator, contact solver and sleep
  logic, plus the character controller, by reading the code.

For each hit:

1. The callers were traced to find out whether the code runs every frame or
   only once on an event (a hit, a state change, a level load).
2. Every-frame hits were simulated at 30, 60, 144, 240, 1000 and 3000 fps.

## The eight bugs

For each one, the original instructions are shown first, then the patched ones.

### 1. Push block slide (`796b20`)

After the player lets go of a push block, its x/z velocity is multiplied by 0.9
every frame:

```
796e31  fld   dword ptr [esp+0x10]
796e35  fld   qword ptr [0xa72790]      ; 0.9
796e3b  fmul  st(1), st
796e3d  fxch  st(1)
796e3f  fstp  dword ptr [esp+0x10]
```

At 144 fps the velocity falls 2.4 times faster per second, so the block slides
less than half as far. The patch replaces the constant load with a call that
returns `0.9^(60·dt)`, using the function's own `dt` argument (`[ebp+8]`):

```
796e35  call  0x13e2030                 ; st0 = 0.9^(60*dt)
796e3a  nop
```

### 2. Push-start timer (`82bd60`)

When the player walks into a block, a timer at `this+0x83c` must fill up before
the block starts to move. When `dt ≤ 1/30` the original adds `2/30` per frame
(written as `dt * (1/dt) / 30 * 2`). Otherwise it adds `2·dt`:

```
82bfe4  fld   st(0)
82bfe6  fld1
82bfe8  fdivrp st(1), st                ; 1/dt
82bfea  fdiv  qword ptr [0xa73cd8]      ; /30
82bff0  fadd  st(0), st                 ; *2
82bff2  fmulp st(1), st                 ; *dt  -> 2/30 regardless of dt
82bff4  jmp   0x82bff8
82bff6  fadd  st(0), st                 ; dt > 1/30: 2*dt
82bff8  fadd  dword ptr [esi+0x83c]
```

At 60 fps the timer gains 4 per second. At 144 fps it gains 9.6 per second, so
blocks start moving 2.4 times sooner. The patched branch adds
`min(4·dt, 2/30)`:

- the same as before at 60 fps and below
- 4 per second above 60 fps

The new code fits in the original 18 bytes, so no call is needed:

```
82bfe4  fadd  st, st(0)
82bfe6  fadd  st, st(0)                 ; 4*dt
82bfe8  fld   dword ptr [0x13e2008]     ; 2/30
82bfee  fcomi st, st(1)
82bff0  fcmovnb st, st(1)               ; min(2/30, 4*dt)
82bff2  fstp  st(1)
82bff4  jmp   0x82bff8                  ; unchanged
```

### 3. Vehicle turning (`834350`)

While steering, a vehicle's angular velocity is multiplied by 0.9 every frame.
The vehicle object (`cLegoVehicle`) stores the frame's `dt` at `this+0x110`
(written by `850360`).

```
8345cd  fld   dword ptr [eax+0x30]
8345d0  fld   qword ptr [0xa72790]      ; 0.9
8345d6  fmul  st(1), st
```

The patched version calls into the new code. The stub loads `this` from the
caller's stack (`[esp+0x3c]` there), reads `dt` from `this+0x110`, and returns
`0.9^(60·dt)`:

```
8345d0  call  0x13e2040
8345d5  nop
```

### 4. Vehicle free movement (`850360`)

In one movement state, the vehicle fields `+0x14c` and `+0x154` are both
multiplied by 0.8 every frame:

```
850484  fld   dword ptr [edi+0x14c]
85048a  fld   qword ptr [0xa72c60]      ; 0.8
850490  fmul  st(1), st
850492  fxch  st(1)
850494  fstp  dword ptr [edi+0x14c]
85049a  fmul  dword ptr [edi+0x154]
8504a0  fstp  dword ptr [edi+0x154]
```

The same factor is used for both fields, so one call that returns
`0.8^(60·dt)` fixes both:

```
85048a  call  0x13e2060
85048f  nop
```

### 5. Physics step below 1 ms (`9a1340`)

The rigid-body world step clamps its `dt` to `[0.001, 1/30]` and steps once
per frame:

```
9a1387  fld   dword ptr [0xa9c944]      ; 1/30
9a138d  fld   dword ptr [ebp+8]         ; dt
9a1390  fcom  st(1)
...                                     ; if dt > 1/30: dt = 1/30
9a13a2  fld   dword ptr [0xa77358]      ; 0.001
9a13a8  fcom  st(1)
...                                     ; if dt < 0.001: dt = 0.001
9a13ba  mov   esi, dword ptr [edi+0x34] ; step the world
```

Above 1000 fps each frame still advances the physics by 1 ms. At 3000 fps
everything the physics engine moves therefore runs at 3 times real time:

- pushed and knocked-over objects
- debris
- ragdolls
- animated objects that drive physics bodies

The upper clamp (`1/30`) is fine. It only slows physics down when the game
already runs below 30 fps.

The 51-byte clamp block is replaced by a call to an accumulator. If the call
returns 0, the function exits through its normal "nothing to do" path
(`9a1497`, which is also used when the world is paused):

```
9a1387  call  0x13e2120                 ; step = accumulate(dt)
9a138c  test  eax, eax
9a138e  je    0x9a1497                  ; less than 1 ms collected: skip
9a1394  jmp   0x9a13ba                  ; step the world by [ebp+8]
9a1396  nop ...                         ; (rest of the old block)
```

The fixed-step mode right before it is left as it was. When the flag at
`0xf8f105` is set, it forces `dt = 1/30`.

The game clamps the same step size the same way in three more places:

| Site | Function | Use |
|---|---|---|
| `98f1d7` | `98f1a0` | character push-out |
| `56296a` | `562950` | ragdoll bones |
| `53f8a0` | `53f8a0` | kinematic bodies; called with a `float *dt` from `56ab50` |

Each one computes a velocity as `distance / dt`, which the physics step then
integrates. In each one, the `fld [1/30]` that starts the clamp is replaced by
a call. The call adds the accumulated time to `dt` first and then performs the
replaced load:

```
98f1d7  call  0x13e2160                 ; [ebp+8] += ACC ; fld [1/30]
56296a  call  0x13e2160
53f8a0  call  0x13e2180                 ; *dt += ACC     ; fld [1/30]
```

See [The physics step accumulator](#the-physics-step-accumulator) for why this
keeps them consistent.

### 6. Path-follow offset (`786b07`)

Objects that follow a set path add `x · 0.0025` to a height offset every frame,
and a time-based smoothing pulls the offset back. The resting point is where
the two balance. Because one side counts frames and the other counts seconds,
the resting offset grows with the frame rate:

- 0.015 at 60 fps
- 0.25 at 1000 fps

```
786b03  fld   dword ptr [esp+0x28]
786b07  fmul  qword ptr [0xbf0b10]      ; 0.0025
786b0d  fadd  dword ptr [esi+0x28]
786b10  fstp  dword ptr [esi+0x28]
```

The patched code multiplies by `0.15 · dt` instead (`0.0025 × 60`):

```
786b07  call  0x13e2080                 ; st0 = x * dt * 0.15
786b0c  nop
```

### 7. Smoothed character height (`99edc0`)

The character controller keeps a smoothed copy of its height (`+0xf4`) and
moves it 50% of the way to the real height (`+0x74`) every frame:

```
99ef57  fld   dword ptr [esi+0x74]
99ef5b  fsub  dword ptr [esi+0xf4]      ; gap
...
99ef6b  fmul  dword ptr [0xe2791c]      ; 0.5
99ef71  fadd  dword ptr [esi+0xf4]
99ef77  fstp  dword ptr [esi+0xf4]
```

The patched code multiplies the gap by `1 − 0.5^(60·dt)`. That is exactly 0.5 at
60 fps, and the same catch-up per second at any other frame rate. The snap-to-
ground case, when the ground body has `(+0x19 & 0x60) == 0x40`, is a separate
path and is not changed.

```
99ef6b  call  0x13e2090                 ; st0 = gap * (1 - 0.5^(60*dt))
99ef70  nop
```

### 8. Critter movement (`44cfa2`, `433b57`, `434049`)

Ambient critters (crabs, "KreepyKrawly" bugs and similar creatures) use one AI
state machine with three movement states. All three read the creature's speed
(`+0x10c`) and use it as a distance per frame:

```
44cf9c  fld   dword ptr [edi+0x10c]
44cfa2  fstp  dword ptr [esp+0x18]
44cfa6  push  ecx
```

```
433b51  fld   dword ptr [edi+0x10c]     ; same in 434043
433b57  fstp  dword ptr [esp+0x18]
433b5b  mov   eax, dword ptr [esp+0x1c]
```

The patched code stores `speed · 60 · dt` instead, then performs the
instructions it replaced:

```
44cfa2  call  0x13e20b0                 ; [esp+0x18] = speed*60*dt ; push ecx
433b57  call  0x13e20d0                 ; [esp+0x18] = speed*60*dt ; mov eax,[esp+0x1c]
434049  call  0x13e20d0
```

#### The `push ecx` stub

The stub at `0x13e20b0` has to leave `ecx` pushed on the caller's stack after
it returns. It does that like this:

1. Push `ecx`.
2. Swap `ecx` with the return address, in two `xchg` steps.
3. Return.

The return address now sits on top of the stack, and the caller's `ecx` value
sits underneath it.

The critters' acceleration uses a separate per-frame byte counter (`+0x46`).
It only affects how quickly they reach full speed, which takes a fraction of a
second, so it was left alone.

## The new code

The patch adds a code section, `.fpsfix`, at `0x13e2000`. All of it is x87
assembly written by hand.

| Offset | Content |
|---|---|
| `+0x00` | float `60·log2(0.9f)` |
| `+0x04` | float `60·log2(0.8f)` |
| `+0x08` | float `2/30` |
| `+0x0c` | float `-60` (= `60·log2(0.5)`) |
| `+0x10` | `exp2`: `st0 = 2^st0` |
| `+0x24` | float `60` |
| `+0x28` | float `0.15` |
| `+0x30` | push block stub |
| `+0x40` | vehicle turn stub |
| `+0x60` | vehicle free stub |
| `+0x80` | path-follow stub |
| `+0x90` | capsule-height stub |
| `+0xb0` | critter stub (`push ecx` form) |
| `+0xd0` | critter stub (`mov eax` form) |
| `+0x100` | signature `LEGOPiratesFPSPatch 1.0.0` |
| `+0x120` | physics step accumulator |
| `+0x160` | accumulator add, `[ebp+8]` form |
| `+0x180` | accumulator add, `float *` form |

### Frame-rate independent decay

A per-frame factor `c` becomes `c^(60·dt)`, which is computed as
`2^(dt · 60·log2(c))`. The constants are computed from the single-precision
values the game itself uses (`0.9f`, `0.8f`). At 60 fps the result is therefore
the original factor, to within one ulp.

`exp2` handles any finite input and any x87 rounding mode. It splits `y` into
an integer and a fraction, then rebuilds `2^y`:

```
fld    st(0)         ; y, y
frndint              ; n, y
fsub   st(1), st     ; n, f = y - n        (|f| <= 1 in every rounding mode)
fxch   st(1)         ; f, n
f2xm1                ; 2^f - 1, n
fld1
faddp  st(1), st     ; 2^f, n
fscale               ; 2^f * 2^n, n
fstp   st(1)         ; 2^y
ret
```

Every stub keeps the x87 stack balanced and uses only registers that the code
around each site has finished with:

- `edx` at the vehicle turn site, where it is not used again
- `ecx` at the kinematic-body site (`53f8a0`), where the next instruction
  reloads it
- `eax` at the physics step, where the original code overwrote it with
  `fnstsw ax`, and at two critter sites, where the replaced instruction loads it

The `push ecx` critter stub uses `ecx` too, but it puts the original value
back.

## The physics step accumulator

`ACC` is a float at `0x13e3000`, in a new zero-initialised data section,
`.fpsdat`. The step stub at `9a1387` does this:

```
a = dt + ACC
if a < 0.001:          ACC = a;  return 0     ; skip this frame's step
else:  dt = min(a, 1/30);  ACC = 0;  return 1 ; step the world by dt
```

Below 1000 fps, `dt ≥ 0.001` on every frame, so `ACC` stays 0. Every frame
then steps by `min(dt, 1/30)`, exactly as in the original. Above 1000 fps,
frames are collected until at least 1 ms has passed. The world then steps by
the real time collected, so physics runs at real time at any frame rate.

The world is never stepped by more time than has really passed. The only
exception is the original `1/30` upper limit, which still applies below 30 fps.

### Consistency with the other three 1 ms clamps

The other three clamp sites compute a velocity of `distance / step`. That
velocity has to match the step that later integrates it, or the body overshoots
or falls short. Each site reads `dt + ACC` at the moment it runs. This is
correct whether the site runs before or after the world step in the frame:

- **Site runs before the step.** `dt + ACC` is exactly the step size that
  `9a1340` is about to use. If no step happens this frame, the velocity is
  recomputed next frame, before anything integrates it.
- **Site runs after the step.** The velocity is integrated by the next frame's
  step, which will be `ACC_after + dt_next`. The site sees `ACC_after + dt`:
  - after a step, `ACC_after = 0`, so the site sees `dt`
  - after a skipped frame, `ACC_after = ACC_before + dt`

  In both cases the value matches the next step whenever the frame time is
  steady.

Below 1000 fps `ACC` is always 0, and all three sites behave exactly as in the
original.

For reference, `9a1340` is called only from `56f090`. That function clamps `dt`
to at most `1/15`, steps the world, and then calls `56c520`, which only adds
`dt` to a timer.

## Jumping

Players report that jumping and pushing become hard with VSync off. The jump
code was checked from 30 fps to several thousand fps:

- **Take-off** sets the vertical speed once. It is not added per frame.
- **Gravity** (`916da0`) is `vy = vy + g·dt`.
- **Apex hang.** The hang timer at the top of the jump (`+0x7f4`, in `7ba0e0`)
  is decremented by `dt`.
- **Ground snap.** The snap-to-ground strength uses an `if dt ≥ 1/60` formula,
  so above 60 fps it uses the 60 fps value. That value is then blended with
  `min(dt·k, 1)` (`6af4e0`), which is time-based.
- **Landing.**
  - The jump state lands only on real collision contact.
  - Other airborne states need at least 0.1 s of real time in the air, plus a
    downward-velocity check.
  - The 0.025-unit "near ground" band (`6b34a0`) is used mostly for
    animation and footsteps. Landing never relies on it alone, so it cannot
    end a jump right after take-off.
- **Zeroing vertical speed.** `8bb1b0` sets vertical speed to 0 only for
  characters with the `+0x54` flag, and only while their ground flag is set.

The jump arc differs between frame rates only through normal integration
rounding. At 60 fps that is at most about `v0·dt/2` of apex height. Higher frame
rates follow the ideal arc more closely.

What did change with the frame rate is what you jump on and push:

- the 1 ms physics step (bug 5): above 1000 fps pushed and knocked objects ran
  faster than real time
- the smoothed character height (bug 7)
- push blocks (bugs 1 and 2)

## Checked and correct already

These scan hits looked frame-rate dependent but are correct:

| Where | What | Why it is fine |
|---|---|---|
| `6de510` | velocity × 0.65 | A character-contact limiter for objects with type flag `0x40000000`. It cuts speed only while the character moves at 70% or more of top speed, and stops once speed falls below that. Simulated from 30 to 3000 fps, the average speed while passing through is about 0.55 to 0.59 of top speed at every rate. |
| `927470` | × 0.75 | Applied to a sort table that is rebuilt from scratch every frame. |
| `86de90` | × 0.5 | Runs once, on a state change. |
| `8a4b10` | velocity × 0.5 | Runs once, on a hit. |
| `74f490` | × 0.5 | Update callback for objects that `78d9a0` sends along a path. Both halved values (a height offset and the velocity at `+0x84`) are recomputed from scratch every frame, so the factor does not compound. |
| `6366e0` | additive change | Runs only on game message `0x7d7`. |
| `650350` | timers + 1/60 | The `1/60` is overwritten with the real frame time before it is used. |
| `530560` | rotation sum | Adds per-frame rotation deltas, so the total is the same at any frame rate. |
| `42cbb0` | camera sway | A time-based sine added to a camera matrix that is rebuilt every frame. |
| `8b2810` | 0.05 / 0.15 / 0.3 | Thresholds on a collision result. The only movement term is already `× dt`. |
| `6f4f10` | 0.9, 0.17 | Push-block listener. Both values are comparison thresholds, not per-frame factors. |
| `524620` | FMV fade | Cosmetic and time-based. |
| 39 functions | hard-coded `1/60`, `1/30` | Fixed-step helpers, upper clamps, or values replaced by the real `dt` first. |
| 106 uses | frame counter | Used for 1–2 frame animation overrides and per-frame bookkeeping. None is a gameplay timer. |
| `9a1340` and callees | physics engine | The integrator, contact solver and sleep logic all scale by the step size. Only the 1 ms minimum was a problem (bug 5). |
| camera, walking, running, animation | — | All scaled by `dt`. |

## Frame-rate dependent but visual only (not patched)

These run per frame but only change how things look, not how the game plays.
They were left alone to keep the patch small:

- `40e150`: particle and splash offset
- `65a300`: animation lean, which eases in faster at high frame rates
- `89d6d0`: sparkle jitter
- `729d40`: texture (UV) scroll offset
- `6af530`: a cosmetic blend
- critter acceleration counter (`+0x46`)

## Testing

- **Disassembly.** Every patch site and the whole new section were
  disassembled after patching (the listings above come from that output).
  Every jump target and call target was checked.
- **Numeric harness.** A small native test program loads the new section's
  bytes from a patched exe and calls each stub with game-like stack frames. The
  results are checked against the formulas above. Each stub is run under three
  x87 control words: `0x037f` (default), `0x027f` (53-bit) and `0x007f`
  (24-bit, which Direct3D 9 sets).
  - **Accumulator:** a sequence of 20 frame times from 0 to 100 ms, run
    under each control word (60 steps). It includes runs of 0.2 ms and
    0.4 ms frames, exactly 1 ms, just below 1 ms, and 0.
  - **Accumulator add:** 5 `(dt, ACC)` cases through both add stubs, under
    each control word (30 calls).
  - **Frame times:** 12 frame times from 0 to 0.1 s for every other stub,
    under each control word (36 rows).
  - **60 fps check:** at exactly `1/60` the push, vehicle and capsule stubs
    return 0.900000, 0.800000 and 0.500000.
- **Both patchers.** `patch_fps.py` and `patch_fps.ps1` produce byte-identical
  files from both supported inputs. Checks done with both:
  - re-running reports "already patched"
  - `--check` writes nothing
  - `--restore` gives back the original hash
  - an exe patched by a different version of the patcher is rebuilt from the
    backup
  - the Steam exe is refused
- **In game.** The patched exe was launched and runs.

## Exe layout after patching

| Section | RVA | Size | Flags |
|---|---|---|---|
| `.fpsfix` | `0xFE2000` | `0x200` | code, read, execute (`0x60000020`) |
| `.fpsdat` | `0xFE3000` | `0x10` virtual, no file data | read, write, zero-initialised (`0xC0000080`) |

- `SizeOfImage` is updated.
- The PE checksum would be recomputed if the input had one. v1.1.0.0 has 0,
  and it stays 0.
- The Large Address Aware flag is kept as it was, or set with `--laa`.
- Nothing else in the header changes.
