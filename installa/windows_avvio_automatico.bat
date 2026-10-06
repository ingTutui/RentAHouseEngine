@echo off
rem Radar Affitti: controllo automatico ogni 30 minuti su questo PC (doppio clic). Serve Python 3.14.
cd /d "%~dp0.."
py -3.14 --version >nul 2>nul
if %errorlevel%==0 (py -3.14 radar.py --installa-avvio) else (python radar.py --installa-avvio)
pause
