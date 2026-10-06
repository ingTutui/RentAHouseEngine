@echo off
rem Un controllo completo (usato dall'avvio automatico di Windows)
cd /d "%~dp0"
where py >nul 2>nul && (py -3 radar.py >> radar_console.log 2>&1) || (python radar.py >> radar_console.log 2>&1)
