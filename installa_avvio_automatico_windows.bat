@echo off
rem Crea un'attivita' di Windows che lancia Radar Affitti ogni 30 minuti
cd /d "%~dp0"
schtasks /Create /SC MINUTE /MO 30 /TN "RadarAffitti" /TR "\"%~dp0avvia_una_volta.bat\"" /F
echo.
echo Fatto: Radar Affitti partira' ogni 30 minuti mentre il PC e' acceso.
echo Per toglierlo:  schtasks /Delete /TN RadarAffitti /F
pause
