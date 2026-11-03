@echo off
cd /d "%~dp0"
if "%~1"=="" (
  py -3.12 freemodel_farmer.py report
) else (
  py -3.12 freemodel_farmer.py %*
)
pause

rem Contoh: run.cmd run --count 5 --concurrency 5
rem Auto-sync ke router setelah farm selesai:
rem py -3.12 sync_router.py
