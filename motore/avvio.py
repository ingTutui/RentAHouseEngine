"""Avvio automatico ogni 30 minuti: Utilita' di pianificazione su Windows, crontab su Linux e Mac.

python radar.py --installa-avvio   crea (o sostituisce) l'attivita' "RadarAffitti"
python radar.py --togli-avvio      la toglie
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

NOME = "RadarAffitti"
SEGNO_CRON = "# RadarAffitti"


def _xml_windows(python: str, script: Path, cartella: Path, minuti: int) -> str:
    inizio = datetime.now().replace(second=0, microsecond=0).isoformat()
    utente = os.environ.get("USERNAME", "")
    if utente and os.environ.get("USERDOMAIN"):
        utente = os.environ["USERDOMAIN"] + "\\" + utente
    id_utente = f"<UserId>{escape(utente)}</UserId>" if utente else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Radar Affitti: nuovi annunci in affitto ogni {minuti} minuti</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{minuti}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{inizio}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">{id_utente}<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>{escape(f'"{script}" --servizio')}</Arguments>
      <WorkingDirectory>{escape(str(cartella))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def installa(cartella: Path, minuti: int = 30) -> int:
    script = cartella / "radar.py"
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        python = str(pythonw if pythonw.exists() else Path(sys.executable))
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
            f.write(_xml_windows(python, script, cartella, minuti))
            xml = f.name
        try:
            r = subprocess.run(["schtasks", "/Create", "/F", "/TN", NOME, "/XML", xml], capture_output=True,
                               text=True, errors="replace")
        finally:
            os.unlink(xml)
        if r.returncode != 0:
            print("Non sono riuscito a creare l'attivita':", (r.stdout + r.stderr).strip())
            return 1
        print(f"Fatto: Windows lancera' il radar ogni {minuti} minuti (attivita' '{NOME}', senza finestre).")
        print("Funziona mentre sei collegata al PC. Il diario dei controlli e' in stato\\radar.log.")
        return 0
    riga = f'*/{minuti} * * * * cd "{cartella}" && "{sys.executable}" radar.py --servizio >/dev/null 2>&1 {SEGNO_CRON}'
    attuale = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace")
    righe = [r for r in (attuale.stdout.splitlines() if attuale.returncode == 0 else []) if SEGNO_CRON not in r]
    r = subprocess.run(["crontab", "-"], input="\n".join(righe + [riga]) + "\n", capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        print("Non sono riuscito a modificare crontab:", r.stderr.strip())
        return 1
    print(f"Fatto: cron lancera' il radar ogni {minuti} minuti. Il diario dei controlli e' in stato/radar.log.")
    if sys.platform == "darwin":
        print("Su Mac tieni la cartella fuori da Documenti, Scrivania e Download, altrimenti macOS blocca cron.")
    return 0


def togli() -> int:
    if os.name == "nt":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", NOME], capture_output=True, text=True,
                           errors="replace")
        print("Avvio automatico tolto." if r.returncode == 0 else "Nessuna attivita' RadarAffitti da togliere.")
        return 0
    attuale = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace")
    if attuale.returncode != 0:
        print("Nessun avvio automatico da togliere.")
        return 0
    righe = [r for r in attuale.stdout.splitlines() if SEGNO_CRON not in r]
    subprocess.run(["crontab", "-"], input="\n".join(righe) + ("\n" if righe else ""), text=True)
    print("Avvio automatico tolto.")
    return 0
