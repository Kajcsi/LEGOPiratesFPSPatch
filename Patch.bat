@echo off
rem LEGO Pirates of the Caribbean high frame rate fix.
rem Put this folder's files next to LEGOPirates.exe and double-click Patch.bat,
rem or drag LEGOPirates.exe onto Patch.bat.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_fps.ps1" %*
echo.
pause
