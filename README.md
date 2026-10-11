# LEGO Pirates of the Caribbean: high frame rate fix

A small patcher for **LEGO Pirates of the Caribbean: The Video Game** (PC, v1.1.0.0).
It fixes the places where the game's physics change with your frame rate, so the
game plays the same at 144 fps, 240 fps or thousands of fps (VSync off) as it does at 60 fps.

**This is not a frame cap.** The game still renders as fast as your monitor or
VSync allows. Instead, the physics code that was written as "per frame" is
changed to "per second".

No game files are included. The patcher edits your own `LEGOPirates.exe` and
keeps a backup of it.

## What it fixes

The game already moves almost everything using real elapsed time, so most of it
works at any frame rate. A full audit of the exe found eight places that don't.
They apply a fixed amount **every frame**, or assume a frame is never shorter
than 1 ms:

| # | What you notice at high frame rates | Cause in the exe |
|---|---|---|
| 1 | **Push blocks** stop sooner after you let go (at 144 fps they slide less than half as far) | Block velocity × 0.9 every frame |
| 2 | **Push blocks** start moving sooner when you walk into them (2.4× sooner at 144 fps) | Push-start timer + 2/30 every frame |
| 3 | **Vehicles / rideables** turn less (spin is damped harder) | Turning speed × 0.9 every frame |
| 4 | **Vehicles / rideables** lose speed faster in one movement state | Two motion values × 0.8 every frame |
| 5 | **Above 1000 fps** (VSync off), everything the physics engine moves runs too fast: pushed and knocked-over objects, debris, ragdolls, and animated objects that drive physics bodies. At 3000 fps it runs 3× real time | The physics step is clamped to at least 1 ms and runs once per frame |
| 6 | Objects that **follow a set path** settle at an offset that grows with the frame rate | + 0.0025 added to the offset every frame |
| 7 | The character controller's **smoothed height** catches up with the real height faster than intended | Smoothed height moves 50% of the gap every frame |
| 8 | **Critters** (crabs, bugs and other small ambient creatures) scurry faster | Move speed used as a distance per frame |

After patching, each of these runs at the per-second rate it has at 60 fps.
At exactly 60 fps the patched game produces the same numbers as the original.
Below 60 fps, the fixes for 1, 3, 4, 6, 7 and 8 also correct the opposite
problem (too little per frame). Fix 2 only changes the game above 60 fps, and
fix 5 only above 1000 fps. Below those rates both behave exactly as in the original.

### Jumping

Players report that with VSync off, pushing objects and jumping can become
almost impossible. The jump code itself was checked closely for this, at every
frame rate from 30 fps to several thousand fps. It already uses real time:

- Take-off speed is set once.
- Gravity adds `g × frame time`.
- The hang time at the top of a jump counts real seconds.
- Landing needs real ground contact (or a minimum time in the air).

What did change with the frame rate is the code around the jump:

- the 1 ms physics step for the objects you jump on and push (fix 5)
- the character's smoothed height (fix 7)
- push blocks (fixes 1 and 2)

All of those are fixed. The jump arc itself differs between frame rates only
by normal integration rounding. Higher frame rates follow the ideal arc
slightly more closely.

Everything else that was checked and found to be correct already is listed in
[TECHNICAL.md](TECHNICAL.md). That includes walking and running, animation speed,
the physics solver, camera smoothing, timers and frame counters.

## Which exe works

The patcher only changes the unencrypted v1.1.0.0 `LEGOPirates.exe` (the GOG
release). It checks every byte it is going to change, plus the bytes around
each site, before writing anything.

| Input exe | SHA-256 |
|---|---|
| v1.1.0.0 (GOG) | `39de791e932a45af6dc5c002d701cd6cdac821b92f7b84166599065348dedc69` |
| v1.1.0.0 (GOG) with the Large Address Aware flag set | `c9a1ffc7de654e0dfc70304a56d6cc8a063eb191a3e96b96c0534da5bcb3c9c5` |

| Result | SHA-256 |
|---|---|
| Patched GOG exe | `c8cff4bdb51e5f572c7792d58775edcee87a1b092346049a26c1f70f78da139a` |
| Patched GOG exe with Large Address Aware (input already LAA, or `--laa` / `-LAA` used) | `acdac4eb18fceb97c21f288c5f4114f6eb8e9a58f82afc2a8cbea610f41ddd8d` |

Any other exe is reported as unsupported and left unchanged. That includes the
exe in the Steam release, whose code section is stored encrypted, so the patch
sites can't be checked.

## How to use

### Windows, nothing to install

1. Download `Patch.bat`, `Restore.bat` and `patch_fps.ps1` (or the whole repo as a ZIP).
2. Put the three files in the game folder, next to `LEGOPirates.exe`.
3. Double-click **Patch.bat**.

You can also drag `LEGOPirates.exe` onto `Patch.bat` from anywhere.

The window shows what it did. A successful run looks like this:

```
LEGO Pirates of the Caribbean high frame rate fix 1.0.0
exe:     ...\LEGOPirates.exe
sha256:  39de791e...  v1.1.0.0 (GOG)
backup:  ...\LEGOPirates.exe.prefps
patched: sha256 c8cff4bd...
Done. The game now runs its per-frame physics at any frame rate.
```

To undo, double-click **Restore.bat**. It puts `LEGOPirates.exe.prefps` back.

PowerShell options, if you prefer the command line:

```powershell
powershell -ExecutionPolicy Bypass -File patch_fps.ps1 "D:\Games\LEGO Pirates"          # patch
powershell -ExecutionPolicy Bypass -File patch_fps.ps1 -Check "D:\Games\LEGO Pirates"   # report only
powershell -ExecutionPolicy Bypass -File patch_fps.ps1 -LAA "D:\Games\LEGO Pirates"     # patch + 4 GB flag
powershell -ExecutionPolicy Bypass -File patch_fps.ps1 -Restore "D:\Games\LEGO Pirates" # undo
```

### Python (any OS, 3.6+)

```
python patch_fps.py "path/to/LEGOPirates.exe"            # patch (a game folder works too)
python patch_fps.py --check "path/to/LEGOPirates.exe"    # report only, writes nothing
python patch_fps.py --laa "path/to/LEGOPirates.exe"      # patch and set Large Address Aware
python patch_fps.py --restore "path/to/LEGOPirates.exe"  # undo
```

Both patchers produce byte-identical results.

## Notes

- **Backup.** The original is saved as `LEGOPirates.exe.prefps` the first
  time you patch. It is never overwritten, so running the patcher again is safe.
- **Large Address Aware (`--laa` / `-LAA`).** This optional flag lets the
  32-bit game use up to 4 GB of RAM instead of 2 GB. That is a common
  stability tweak for this game. It does not affect the frame rate fix.
- **Frame rate.** Use whatever refresh rate and VSync setting you like. The
  fix has no cap and no target frame rate.
- **Save games** are not touched.
- **Antivirus.** The patch adds a small code section to the exe. If your
  antivirus objects to the modified exe, restore the backup.

## How it works (short version)

- **Per-frame factors.** Each per-frame factor `c` becomes `c^(60 × dt)`,
  where `dt` is the frame time the game already computes. That gives the same
  amount per second as at 60 fps, at any frame rate. Per-frame amounts
  (fixes 6 and 8) are multiplied by `60 × dt`.
- **Push-start timer.** It changes from `+2/30` per frame to
  `+min(4 × dt, 2/30)`.
- **Physics step (fix 5).** It gets an accumulator. Frame time is
  collected until it reaches the engine's 1 ms minimum step, then the world
  steps once by the collected time. Below 1000 fps every frame steps exactly as
  before.

The patch adds two sections to the exe: `.fpsfix` (512 bytes of code and
constants) and `.fpsdat` (16 bytes of state). Each patched instruction becomes a
`call` into the new code. Full details, before/after disassembly and the test
results are in [TECHNICAL.md](TECHNICAL.md).
