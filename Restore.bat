@echo off
rem Undo the high frame rate fix: puts LEGOPirates.exe.prefps back as LEGOPirates.exe.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_fps.ps1" -Restore %*
echo.
pause
