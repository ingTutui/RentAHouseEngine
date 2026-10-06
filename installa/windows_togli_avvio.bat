@echo off
rem Radar Affitti: toglie il controllo automatico da questo PC (doppio clic).
cd /d "%~dp0.."
py -3.14 --version >nul 2>nul
if %errorlevel%==0 (py -3.14 radar.py --togli-avvio) else (python radar.py --togli-avvio)
pause
