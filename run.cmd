@echo off
py -3.12 freemodel_farmer.py
pause

rem Menjalankan auto-sync ke router.aestc.id setelah farm selesai
py -3.12 sync_router.py
