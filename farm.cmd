@echo off
rem ============================================================
rem  Freemodel Farm - LAUNCHER
rem  Klik 2x file ini: masuk UI interaktif (menu opsi + satu klik farm).
rem  Langsung farm tanpa menu: farm-auto.cmd
rem ============================================================
cd /d "%~dp0"

mode con: cols=100 lines=36 >nul 2>nul

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher "py" tidak ditemukan. Install Python 3.12.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   FREEMODEL-FARM  -  UI interaktif
echo   f = mulai farm    q = keluar
echo ============================================================
echo.

py -3.12 farm_tui.py
pause
