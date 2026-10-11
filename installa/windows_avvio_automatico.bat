@echo off
rem Radar Affitti: controllo automatico (intervallo_minuti in config.toml) su questo PC (doppio clic). Serve uv.
cd /d "%~dp0.."
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" --version >nul 2>nul
if errorlevel 1 goto manca
"%UV%" run python radar.py --installa-avvio
goto fine
:manca
echo Manca uv, il programma che prepara Python e le librerie.
echo Installalo con questo comando in PowerShell:  winget install --id=astral-sh.uv -e
echo Poi chiudi questa finestra e rilancia questo file.
:fine
pause
