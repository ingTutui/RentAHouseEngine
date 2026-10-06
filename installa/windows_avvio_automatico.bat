@echo off
rem Radar Affitti: controllo automatico ogni 30 minuti su questo PC (doppio clic)
cd /d "%~dp0.."
where py >nul 2>nul
if %errorlevel%==0 (py -3 radar.py --installa-avvio) else (python radar.py --installa-avvio)
pause
