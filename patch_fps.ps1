<#
.SYNOPSIS
    LEGO Pirates of the Caribbean: The Video Game - high frame rate fix.

.DESCRIPTION
    Same patch as patch_fps.py, for Windows PowerShell 5.1 or newer (no Python
    needed). Fixes the places in LEGOPirates.exe (v1.1.0.0) where game physics
    depend on the frame rate, so the game behaves the same at 144, 240 or 1000+
    fps as it does at 60 fps. No frame cap is added.

    The original exe is copied to LEGOPirates.exe.prefps before anything is
    written. An exe patched by a different version of this script is rebuilt from
    that backup. See README.md and TECHNICAL.md for what is changed and why.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File patch_fps.ps1 "D:\Games\LEGO Pirates\LEGOPirates.exe"
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File patch_fps.ps1 -Restore "D:\Games\LEGO Pirates"
#>
[CmdletBinding()]
param(
    # Path to LEGOPirates.exe or to the game folder. Default: current folder, then this script's folder.
    [Parameter(Position = 0)][string]$Path,
    # Put the backed-up original exe back.
    [switch]$Restore,
    # Only report whether the exe can be patched.
    [switch]$Check,
    # Also set the Large Address Aware flag (lets the 32-bit game use 4 GB of RAM).
    [switch]$LAA
)

$ErrorActionPreference = 'Stop'
$Version      = '1.0.0'
$SectionName  = '.fpsfix'   # new code + constants (read/execute)
$DataName     = '.fpsdat'   # one float of state (read/write, zero-initialised)
$CaveSize     = 0x200
$DataSize     = 0x10
$BackupSuffix = '.prefps'
$ImageBase    = 0x400000
$SigPrefix    = 'LEGOPiratesFPSPatch '
$Sig          = $SigPrefix + $Version
$SigOff       = 0x100

# Game globals the new code reads.
$G_DT   = 0xF76670   # float: this frame's game time step (seconds, after time scale)
$G_1_30 = 0xA9C944   # float 1/30: the physics step's upper clamp
$G_1MS  = 0xA77358   # float 0.001: the physics step's lower clamp

# Known v1.1.0.0 executables this patcher was built and tested against.
$Known = @{
    '39de791e932a45af6dc5c002d701cd6cdac821b92f7b84166599065348dedc69' = 'v1.1.0.0 (GOG)'
    'c9a1ffc7de654e0dfc70304a56d6cc8a063eb191a3e96b96c0534da5bcb3c9c5' = 'v1.1.0.0 (GOG) + Large Address Aware'
}

# Original bytes at every patch site (virtual address -> bytes). Kept in sync with patch_fps.py,
# which has the full comments.
$Sites = [ordered]@{
    0x796E35 = 'dd059027a700'                         # 1. push block:        fld qword [0.9]
    0x82BFE4 = 'd9c0d9e8def1dc35d83ca700dcc0dec9eb02' # 2. push timer:        += 2/30 per frame
    0x8345D0 = 'dd059027a700'                         # 3. vehicle turn:      fld qword [0.9]
    0x85048A = 'dd05602ca700'                         # 4. vehicle free:      fld qword [0.8]
    0x9A1387 = 'd90544c9a900d94508d8d1dfe0f6c4417507ddd8d95d08eb1addd9d9055873a700d8d1dfe0ddd9f6c4417505d95d08eb02ddd8'
                                                      # 5. physics step:      clamp dt to [0.001, 1/30]
    0x98F1D7 = 'd90544c9a900'                         # 5. push-out clamp:    fld [1/30]
    0x56296A = 'd90544c9a900'                         # 5. ragdoll clamp:     fld [1/30]
    0x53F8A0 = 'd90544c9a900'                         # 5. kinematic clamp:   fld [1/30]
    0x786B07 = 'dc0d100bbf00'                         # 6. path follow:       fmul qword [0.0025]
    0x99EF6B = 'd80d1c79e200'                         # 7. capsule height:    fmul dword [0.5]
    0x44CFA2 = 'd95c241851'                           # 8. critter:           fstp [esp+0x18] / push ecx
    0x433B57 = 'd95c24188b44241c'                     # 8. critter:           fstp [esp+0x18] / mov eax,[esp+0x1c]
    0x434049 = 'd95c24188b44241c'                     # 8. critter:           fstp [esp+0x18] / mov eax,[esp+0x1c]
}
# Context bytes that must also match (not modified).
$Context = [ordered]@{
    0x796E31 = 'd9442410'
    0x796E3B = 'dcc9d9c9d95c2410'
    0x796E6D = 'd86508'
    0x82BFCD = 'd90544c9a900d9057066f700'
    0x82BFF6 = 'dcc0d8863c080000d99e3c080000'
    0x834368 = '8974243c'
    0x8345C3 = '8b44243c8b8030020000d94030'
    0x8345D6 = 'dcc9d9c9d95c2440'
    0x8503C4 = 'd99f10010000'
    0x850484 = 'd9874c010000'
    0x850490 = 'dcc9d9c9d99f4c010000d88f54010000d99f54010000'
    0x9A1361 = 'd84d08538b5d0c5657d95d088bf9'
    0x9A137E = 'd9052881e200d95d08'
    0x9A13BA = '8b773485f6'
    0x9A1497 = '8b4c244c5f5e5b33cc'
    0x98F1C5 = '8b450c535657'
    0x98F1DD = 'd94508d8d1dfe0f6c441'
    0x562970 = '8b450cd9450853d8d1'
    0x53F8A6 = '8b4c2404d811dfe0'
    0x786B03 = 'd9442428'
    0x786B0D = 'd84628d95e28'
    0x99EF57 = 'd946745fd8a6f4000000b001d95c2408d9442408'
    0x99EF71 = 'd886f4000000d99ef4000000'
    0x44CF9C = 'd9870c010000'
    0x44CFA7 = 'd902d944241c'
    0x433B51 = 'd9870c010000'
    0x433B5F = 'd94620'
    0x434043 = 'd9870c010000'
    0x434051 = 'd94620'
}

function Log([string]$m) { Write-Host $m }

function Hex([string]$s) {
    $b = New-Object byte[] ($s.Length / 2)
    for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($s.Substring($i * 2, 2), 16) }
    , $b
}
function ToHex([byte[]]$b) { [BitConverter]::ToString($b).Replace('-', '').ToLowerInvariant() }
function Join-Bytes { $r = New-Object System.Collections.Generic.List[byte]; foreach ($a in $args) { $r.AddRange([byte[]]$a) }; , $r.ToArray() }
function LE32([long]$v) { , [BitConverter]::GetBytes([uint32]($v -band 0xFFFFFFFFL)) }
function LE16([long]$v) { , [BitConverter]::GetBytes([uint16]$v) }
function Put([byte[]]$dst, [long]$at, [byte[]]$src) { [Array]::Copy($src, 0, $dst, $at, $src.Length) }
function U16([byte[]]$d, [long]$o) { [long][BitConverter]::ToUInt16($d, $o) }
function U32([byte[]]$d, [long]$o) { [long][BitConverter]::ToUInt32($d, $o) }
function Align([long]$x, [long]$a) { $x + $a - 1 - (($x + $a - 1) % $a) }
function Sha256([byte[]]$b) { ToHex ([Security.Cryptography.SHA256]::Create().ComputeHash($b)) }

function Read-PE([byte[]]$d) {
    if ($d.Length -lt 0x40 -or $d[0] -ne 0x4D -or $d[1] -ne 0x5A) { throw 'not an EXE (no MZ header)' }
    $pe = U32 $d 0x3C
    if ($pe + 0xF8 -gt $d.Length -or (U32 $d $pe) -ne 0x4550) { throw 'not a PE file' }
    $opt = $pe + 24
    if ((U16 $d $opt) -ne 0x10B) { throw 'not a 32-bit executable' }
    $p = [pscustomobject]@{
        D             = $d
        NSecOff       = $pe + 6
        CharsOff      = $pe + 22
        Opt           = $opt
        ImageBase     = U32 $d ($opt + 28)
        SectAlign     = U32 $d ($opt + 32)
        FileAlign     = U32 $d ($opt + 36)
        SizeOfHeaders = U32 $d ($opt + 60)
        DllChars      = U16 $d ($opt + 70)
        SecTable      = $opt + (U16 $d ($pe + 20))
        Sections      = New-Object System.Collections.ArrayList
    }
    $n = U16 $d $p.NSecOff
    for ($i = 0; $i -lt $n; $i++) {
        $o = $p.SecTable + $i * 40
        [void]$p.Sections.Add([pscustomobject]@{
            Name  = [Text.Encoding]::ASCII.GetString($d, $o, 8).TrimEnd([char]0)
            VSize = U32 $d ($o + 8)
            VA    = U32 $d ($o + 12)
            RSize = U32 $d ($o + 16)
            RPtr  = U32 $d ($o + 20)
        })
    }
    $p
}

function Get-Offset($p, [long]$va) {
    $rva = $va - $p.ImageBase
    foreach ($s in $p.Sections) {
        if ($rva -ge $s.VA -and $rva -lt $s.VA + [Math]::Min($s.VSize, $s.RSize)) { return $s.RPtr + $rva - $s.VA }
    }
    throw ('address 0x{0:x} is not in the file' -f $va)
}
function Read-VA($p, [long]$va, [int]$n) {
    $o = Get-Offset $p $va
    $b = New-Object byte[] $n
    [Array]::Copy($p.D, $o, $b, 0, $n)
    , $b
}
function Write-VA($p, [long]$va, [byte[]]$b) { Put $p.D (Get-Offset $p $va) $b }
function Get-Section($p, [string]$name) { $p.Sections | Where-Object { $_.Name -eq $name } | Select-Object -First 1 }

# The patch signature stored in an already patched exe, or $null.
function Read-Sig($p) {
    $s = Get-Section $p $SectionName
    if (-not $s) { return $null }
    try { $raw = Read-VA $p ($p.ImageBase + $s.VA + $SigOff) 32 } catch { return '?' }
    $n = [Array]::IndexOf($raw, [byte]0)
    if ($n -lt 0) { $n = $raw.Length }
    [Text.Encoding]::ASCII.GetString($raw, 0, $n)
}

function Test-Exe($p) {
    if (Get-Section $p $SectionName) { return @('patched') }
    $problems = @()
    if ($p.ImageBase -ne $ImageBase) { $problems += ('image base is 0x{0:x}, expected 0x{1:x}' -f $p.ImageBase, $ImageBase) }
    if ($p.DllChars -band 0x40) { $problems += 'exe is relocatable (ASLR); expected a fixed-base v1.1.0.0 exe' }
    foreach ($table in @($Sites, $Context)) {
        foreach ($e in $table.GetEnumerator()) {
            # (an [ordered] dictionary indexed with an int is positional, so enumerate instead)
            $va = $e.Key; $want = $e.Value
            try { $got = ToHex (Read-VA $p $va ($want.Length / 2)) }
            catch { $problems += $_.Exception.Message; continue }
            if ($got -ne $want) { $problems += ('bytes at 0x{0:x} are {1}, expected {2}' -f $va, $got, $want) }
        }
    }
    if ($problems.Count) { return @('unknown') + $problems }
    @('original')
}

function Build-Cave([long]$sva, [long]$dva) {
    # Code for the new section, assembled by hand (x87, 32-bit). Same bytes as
    # build_cave() in patch_fps.py, which has the instruction-by-instruction comments.
    # pow(c, 60*dt) is evaluated as 2^(dt * 60*log2(c)), so at 60 fps the factor is
    # exactly the original per-frame constant and the decay per second is the same
    # at every frame rate.
    $K09 = $sva + 0x00; $K08 = $sva + 0x04; $C230 = $sva + 0x08; $KM60 = $sva + 0x0C
    $EXP2 = $sva + 0x10; $K60 = $sva + 0x24; $K015 = $sva + 0x28
    $ACC = $dva + 0x00   # float: game time not yet given to the physics step
    $cave = New-Object byte[] $CaveSize
    Put $cave 0x00 (Hex '4aec11c1')   # float 60*log2(0.9f) = -9.1202
    Put $cave 0x04 (Hex '86869ac1')   # float 60*log2(0.8f) = -19.3157
    Put $cave 0x08 (Hex '8988883d')   # float 2/30
    Put $cave 0x0C (Hex '000070c2')   # float -60 = 60*log2(0.5)
    Put $cave 0x24 (Hex '00007042')   # float 60
    Put $cave 0x28 (Hex '9a99193e')   # float 0.15 = 0.0025 * 60
    # exp2: st0 = y -> st0 = 2^y   (fld st0 / frndint / fsub st1,st / fxch / f2xm1 / fld1 / faddp / fscale / fstp st1 / ret)
    Put $cave 0x10 (Hex 'd9c0d9fcdce9d9c9d9f0d9e8dec1d9fdddd9c3')

    # jmp / call exp2 appended to the code $c placed at cave offset $off
    function Exp2Tail([byte[]]$c, [long]$off, [string]$op) { Join-Bytes $c (Hex $op) (LE32 ($EXP2 - ($sva + $off + $c.Length + 5))) }

    $e = @{ C230 = $C230 }
    # 1. 796b20 push block: dt is the function's stack argument [ebp+8].
    $c = Join-Bytes (Hex 'd94508') (Hex 'd80d') (LE32 $K09)                         # fld [ebp+8] / fmul [K09]
    Put $cave 0x30 (Exp2Tail $c 0x30 'e9'); $e.push = $sva + 0x30                   # jmp exp2
    # 3. 834350 vehicle: this = [esp+0x3c] in the caller (+4 for our return address); dt at this+0x110.
    $c = Join-Bytes (Hex '8b542440') (Hex 'd98210010000') (Hex 'd80d') (LE32 $K09)   # mov edx,[esp+0x40] / fld [edx+0x110] / fmul [K09]
    Put $cave 0x40 (Exp2Tail $c 0x40 'e9'); $e.veh_turn = $sva + 0x40
    # 4. 850360 vehicle: edi = this, dt at this+0x110.
    $c = Join-Bytes (Hex 'd98710010000') (Hex 'd80d') (LE32 $K08)                   # fld [edi+0x110] / fmul [K08]
    Put $cave 0x60 (Exp2Tail $c 0x60 'e9'); $e.veh_free = $sva + 0x60
    # 6. 786b07 path follow: x * 0.0025 per frame -> x * 0.15 * dt
    $c = Join-Bytes (Hex 'd80d') (LE32 $G_DT) (Hex 'd80d') (LE32 $K015) (Hex 'c3')  # fmul [dt] / fmul [K015] / ret
    Put $cave 0x80 $c; $e.path = $sva + 0x80
    # 7. 99ef6b capsule height: st0 = target - smoothed; st0 *= 1 - 0.5^(60*dt)
    $c = Join-Bytes (Hex 'd905') (LE32 $G_DT) (Hex 'd80d') (LE32 $KM60)              # fld [dt] / fmul [KM60]
    $c = Exp2Tail $c 0x90 'e8'                                                      # call exp2
    $c = Join-Bytes $c (Hex 'd9e8dee1dec9c3')                                       # fld1 / fsubrp st1,st / fmulp st1,st / ret
    Put $cave 0x90 $c; $e.capsule = $sva + 0x90
    # 8. critters: st0 = move speed; store speed * 60 * dt, then redo the replaced instruction.
    #    44cfa2  fstp [esp+0x18] / push ecx
    $c = Join-Bytes (Hex 'd80d') (LE32 $G_DT) (Hex 'd80d') (LE32 $K60) (Hex 'd95c241c51874c2404870c24c3')
    Put $cave 0xB0 $c; $e.critter_a = $sva + 0xB0                                   # ... fstp [esp+0x1c] / push ecx / xchg ecx,[esp+4] / xchg ecx,[esp] / ret
    #    433b57 / 434049  fstp [esp+0x18] / mov eax,[esp+0x1c]
    $c = Join-Bytes (Hex 'd80d') (LE32 $G_DT) (Hex 'd80d') (LE32 $K60) (Hex 'd95c241c8b442420c3')
    Put $cave 0xD0 $c; $e.critter_b = $sva + 0xD0                                   # ... fstp [esp+0x1c] / mov eax,[esp+0x20] / ret

    Put $cave $SigOff ([Text.Encoding]::ASCII.GetBytes($Sig))

    # 5. Physics step accumulator (9a1340). Frame time is collected in ACC until it
    #    reaches the engine's minimum step of 1 ms; then the world is stepped once by
    #    min(ACC + dt, 1/30) and ACC is cleared. Returns eax = 0 to skip this frame's step.
    $step = Join-Bytes (Hex 'd905') (LE32 $G_1_30) (Hex 'dbf1dbc1d95d08ddd8') `
                       (Hex 'c705') (LE32 $ACC) (LE32 0) (Hex 'b801000000c3')       # fld [1/30] / fcomi / fcmovnb / fstp [ebp+8] / fstp st0 / mov [ACC],0 / mov eax,1 / ret
    $skip = Join-Bytes (Hex 'd91d') (LE32 $ACC) (Hex '33c0c3')                       # fstp [ACC] / xor eax,eax / ret
    $c = Join-Bytes (Hex 'd94508d805') (LE32 $ACC) (Hex 'd905') (LE32 $G_1MS) (Hex 'dff177') ([byte[]]@($step.Length)) $step $skip
    Put $cave 0x120 $c; $e.step = $sva + 0x120                                      # fld [ebp+8] / fadd [ACC] / fld [0.001] / fcomip / ja .skip
    # 5b. 98f1d7 / 56296a: [ebp+8] += ACC, then the replaced fld [1/30].
    $c = Join-Bytes (Hex 'd94508d805') (LE32 $ACC) (Hex 'd95d08d905') (LE32 $G_1_30) (Hex 'c3')
    Put $cave 0x160 $c; $e.acc_ebp = $sva + 0x160
    # 5c. 53f8a0(float *dt): *dt += ACC, then the replaced fld [1/30].
    $c = Join-Bytes (Hex '8b4c2408d901d805') (LE32 $ACC) (Hex 'd919d905') (LE32 $G_1_30) (Hex 'c3')
    Put $cave 0x180 $c; $e.acc_ptr = $sva + 0x180

    @{ Cave = $cave; E = $e }
}

# call target, padded with nops to the length of the replaced bytes
function Call-At([long]$site, [long]$target, [int]$len) {
    $b = Join-Bytes (Hex 'e8') (LE32 ($target - ($site + 5)))
    if ($len -gt 5) { $b = Join-Bytes $b ([byte[]](@(0x90) * ($len - 5))) }
    , $b
}

function Get-PeChecksum([byte[]]$d, [int]$cksOff) {
    if (-not ('FpsFixChecksum' -as [type])) {
        Add-Type -TypeDefinition @'
public static class FpsFixChecksum {
    public static uint Compute(byte[] d, int cks) {
        ulong s = 0; int n = d.Length;
        for (int i = 0; i < n; i += 2) {
            if (i == cks || i == cks + 2) continue;
            uint w = d[i]; if (i + 1 < n) w |= (uint)d[i + 1] << 8;
            s += w; s = (s & 0xFFFF) + (s >> 16);
        }
        s = (s & 0xFFFF) + (s >> 16);
        return (uint)(s + (ulong)n);
    }
}
'@
    }
    [FpsFixChecksum]::Compute($d, $cksOff)
}

function Invoke-Patch($p) {
    $d = $p.D
    $fileLen = [long]$d.Length
    $lastRawEnd = ($p.Sections | ForEach-Object { $_.RPtr + $_.RSize } | Measure-Object -Maximum).Maximum
    if ($fileLen -ne $lastRawEnd) { throw 'exe has extra data after its last section; refusing to append' }
    $nsec = $p.Sections.Count
    $hdr = $p.SecTable + $nsec * 40
    if ($hdr + 80 -gt $p.SizeOfHeaders) { throw 'no room for two new section headers' }
    for ($i = 0; $i -lt 80; $i++) { if ($d[$hdr + $i]) { throw 'no room for two new section headers' } }

    $top     = ($p.Sections | ForEach-Object { $_.VA + $_.VSize } | Measure-Object -Maximum).Maximum
    $codeRva = Align $top $p.SectAlign
    $dataRva = Align ($codeRva + $CaveSize) $p.SectAlign
    $newRaw  = Align $fileLen $p.FileAlign
    $rawSize = Align $CaveSize $p.FileAlign
    $sva     = $p.ImageBase + $codeRva
    $built   = Build-Cave $sva ($p.ImageBase + $dataRva)
    $e       = $built.E

    $out = New-Object byte[] ($newRaw + $rawSize)
    [Array]::Copy($d, $out, $fileLen)
    Put $out $newRaw $built.Cave
    $p.D = $out

    # section headers: name, VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData, 0, 0, 0, 0, characteristics
    $name = New-Object byte[] 8; Put $name 0 ([Text.Encoding]::ASCII.GetBytes($SectionName))
    Put $out $hdr (Join-Bytes $name (LE32 $CaveSize) (LE32 $codeRva) (LE32 $rawSize) (LE32 $newRaw) (LE32 0) (LE32 0) (LE16 0) (LE16 0) (LE32 0x60000020))       # code | execute | read
    $name = New-Object byte[] 8; Put $name 0 ([Text.Encoding]::ASCII.GetBytes($DataName))
    Put $out ($hdr + 40) (Join-Bytes $name (LE32 $DataSize) (LE32 $dataRva) (LE32 0) (LE32 0) (LE32 0) (LE32 0) (LE16 0) (LE16 0) (LE32 0xC0000080L))    # uninitialised data | read | write
    Put $out $p.NSecOff (LE16 ($nsec + 2))
    Put $out ($p.Opt + 56) (LE32 (Align ($dataRva + $DataSize) $p.SectAlign))                   # SizeOfImage
    Put $out ($p.Opt + 4) (LE32 ((U32 $out ($p.Opt + 4)) + $rawSize))                          # SizeOfCode
    Put $out ($p.Opt + 12) (LE32 ((U32 $out ($p.Opt + 12)) + (Align $DataSize $p.FileAlign)))  # SizeOfUninitializedData
    [void]$p.Sections.Add([pscustomobject]@{ Name = $SectionName; VSize = $CaveSize; VA = $codeRva; RSize = $rawSize; RPtr = $newRaw })
    [void]$p.Sections.Add([pscustomobject]@{ Name = $DataName; VSize = $DataSize; VA = $dataRva; RSize = 0; RPtr = 0 })

    $targets = [ordered]@{
        0x796E35 = $e.push
        0x8345D0 = $e.veh_turn
        0x85048A = $e.veh_free
        0x98F1D7 = $e.acc_ebp
        0x56296A = $e.acc_ebp
        0x53F8A0 = $e.acc_ptr
        0x786B07 = $e.path
        0x99EF6B = $e.capsule
        0x44CFA2 = $e.critter_a
        0x433B57 = $e.critter_b
        0x434049 = $e.critter_b
    }
    $done = @{}
    foreach ($t in $targets.GetEnumerator()) {
        $len = 0; foreach ($s in $Sites.GetEnumerator()) { if ($s.Key -eq $t.Key) { $len = $s.Value.Length / 2 } }
        Write-VA $p $t.Key (Call-At $t.Key $t.Value $len)
        $done[[long]$t.Key] = 1
    }
    # 2. 82bfe4 push timer (st0 = dt): += min(4*dt, 2/30) instead of += 2/30.
    # fadd st,st / fadd st,st / fld [2/30] / fcomi st,st1 / fcmovnb st,st1 / fstp st1 / jmp 82bff8
    Write-VA $p 0x82BFE4 (Join-Bytes (Hex 'd8c0d8c0d905') (LE32 $e.C230) (Hex 'dbf1dbc1ddd9eb02'))
    $done[[long]0x82BFE4] = 1
    # 5. 9a1387..9a13b9 (51 bytes): call STEP / test eax,eax / je 9a1497 (no step this frame) / jmp 9a13ba / nops
    $site = 0x9A1387
    $b = Join-Bytes (Hex 'e8') (LE32 ($e.step - ($site + 5))) (Hex '85c00f84') (LE32 (0x9A1497 - ($site + 13))) (Hex 'eb') ([byte[]]@(0x9A13BA - ($site + 15)))
    $b = Join-Bytes $b ([byte[]](@(0x90) * (51 - $b.Length)))
    Write-VA $p $site $b
    $done[[long]$site] = 1
    foreach ($s in $Sites.GetEnumerator()) { if (-not $done.ContainsKey([long]$s.Key)) { throw ('internal error: site 0x{0:x} not patched' -f $s.Key) } }

    # Keep the PE checksum valid if the original had one (v1.1.0.0 has 0).
    if (U32 $out ($p.Opt + 64)) { Put $out ($p.Opt + 64) (LE32 (Get-PeChecksum $out ($p.Opt + 64))) }
}

function Find-Exe([string]$arg) {
    $cwd = (Get-Location -PSProvider FileSystem).ProviderPath
    if ($arg) {
        $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($arg)
        if (Test-Path -LiteralPath $full -PathType Container) { return (Join-Path $full 'LEGOPirates.exe') }
        return $full
    }
    foreach ($dir in @($cwd, $PSScriptRoot)) {
        if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'LEGOPirates.exe') -PathType Leaf)) { return (Join-Path $dir 'LEGOPirates.exe') }
    }
    Join-Path $cwd 'LEGOPirates.exe'
}

function Main {
    Log "LEGO Pirates of the Caribbean high frame rate fix $Version"
    $exe = Find-Exe $Path
    $backup = $exe + $BackupSuffix
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        Log "LEGOPirates.exe not found: $exe"
        Log 'Run this from the game folder or pass the path to the exe.'
        return 2
    }

    if ($Restore) {
        if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) { Log "No backup found ($backup). Nothing to restore."; return 1 }
        Copy-Item -LiteralPath $backup -Destination $exe -Force
        Log "Restored $exe from $backup"
        return 0
    }

    $data = [IO.File]::ReadAllBytes($exe)
    $h = Sha256 $data
    Log "exe:     $exe"
    Log ("sha256:  $h  " + $(if ($Known.ContainsKey($h)) { $Known[$h] } else { '' }))
    $p = Read-PE $data
    $state = @(Test-Exe $p)
    $keepLaa = $false
    if ($state[0] -eq 'patched') {
        $found = Read-Sig $p
        if ($found -ceq $Sig) { Log "This exe is already patched (version $Version). Use -Restore to undo."; return 0 }
        $old = if ($found -and $found.StartsWith($SigPrefix)) { $found.Substring($SigPrefix.Length) } else { 'unknown' }
        Log "This exe has a different version of this patch ($old)."
        if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
            Log "The backup $backup is missing, so it can't be updated."
            Log 'Put the original exe back and run this again.'
            return 1
        }
        $orig = [IO.File]::ReadAllBytes($backup)
        $pOrig = Read-PE $orig
        if (@(Test-Exe $pOrig)[0] -ne 'original') {
            Log "The backup $backup is not an unpatched v1.1.0.0 exe, so nothing was changed."
            return 1
        }
        if ($Check) { Log "OK: run without -Check to update it to $Version (rebuilt from the backup)."; return 0 }
        Log "update:  rebuilding from the backup $backup"
        $keepLaa = [bool]((U16 $p.D $p.CharsOff) -band 0x20)
        $data = $orig; $p = $pOrig
    } elseif ($state[0] -ne 'original') {
        Log 'This exe does not match v1.1.0.0, so it was NOT changed:'
        $state | Select-Object -Skip 1 -First 10 | ForEach-Object { Log "  - $_" }
        Log 'The patch needs the unencrypted v1.1.0.0 LEGOPirates.exe (see README).'
        return 1
    } elseif ($Check) {
        Log 'OK: exe matches v1.1.0.0 and can be patched.'
        return 0
    }

    [void](Invoke-Patch $p)
    if ($LAA -or $keepLaa) { Put $p.D $p.CharsOff (LE16 ((U16 $p.D $p.CharsOff) -bor 0x20)) }

    # verify what we wrote
    $out = $p.D
    $p2 = Read-PE $out
    if ((Read-Sig $p2) -cne $Sig -or -not (Get-Section $p2 $DataName)) {
        Log 'Internal error: patched image failed verification; nothing written.'
        return 3
    }

    if (-not (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $exe -Destination $backup
        Log "backup:  $backup"
    } else {
        Log "backup:  $backup (already exists, kept)"
    }
    $tmp = $exe + '.tmp'
    [IO.File]::WriteAllBytes($tmp, $out)
    Move-Item -LiteralPath $tmp -Destination $exe -Force
    Log "patched: sha256 $(Sha256 $out)"
    Log 'Done. The game now runs its per-frame physics at any frame rate.'
    return 0
}

try { $rc = Main; exit ([int]($rc | Select-Object -Last 1)) }
catch { Log "Error: $($_.Exception.Message)"; exit 3 }
