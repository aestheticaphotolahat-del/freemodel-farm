@echo off
rem ============================================================
rem  Freemodel Farm - 1 KLIK TANPA MENU
rem  Preflight slot, farm semua slot bebas paralel, auto-import router,
rem  lalu tampilkan ringkasan. Tidak ada argumen yang perlu diingat.
rem ============================================================
cd /d "%~dp0"

py -3.12 freemodel_farmer.py preflight
if errorlevel 1 (
  echo.
  echo  [!] Ada slot Telegram yang belum siap.
  echo      Buka config/telegram_accounts.json, isi blok "app" dan
  echo      session_string tiap slot, lalu jalankan:
  echo          py -3.12 tg_session.py tg-XXX
  echo.
  pause
  exit /b 1
)

py -3.12 freemodel_farmer.py run --auto
set RC=%errorlevel%

echo.
py -3.12 freemodel_farmer.py report
echo.
pause
exit /b %RC%
