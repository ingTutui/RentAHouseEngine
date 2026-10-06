"""Modalita' servizio (python radar.py --servizio), quella usata dall'avvio automatico ogni 30 minuti:
un solo controllo alla volta, aggiornamento dal repository, poi il controllo vero e proprio."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .aggiorna import SENZA_FINESTRA, aggiorna
from .archivio import Archivio
from .config import Percorsi
from .controllo import esegui

log = logging.getLogger("radar")
SCADENZA_BLOCCO_SEC = 3 * 3600


class Blocco:
    """File stato/radar.lock: impedisce due controlli contemporanei (es. il primo, che e' lungo)."""

    def __init__(self, path: Path):
        self.path = path
        self.preso = False

    def _vecchio(self) -> bool:
        try:
            eta = time.time() - self.path.stat().st_mtime
            pid = int((self.path.read_text(encoding="utf-8").split() or ["0"])[0])
        except (OSError, ValueError):
            return True
        if eta > SCADENZA_BLOCCO_SEC:
            return True
        if os.name != "nt" and pid:  # su Windows os.kill chiuderebbe il processo: basta la scadenza
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        return False

    def prendi(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M')}\n")
                self.preso = True
                return True
            except FileExistsError:
                if not self._vecchio():
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
        return False

    def lascia(self) -> None:
        if self.preso:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.preso = False


def servizio(cfg: dict, percorsi: Percorsi, segreti: dict[str, str], finto: bool = False) -> int:
    percorsi.prepara()
    blocco = Blocco(percorsi.lock)
    if not blocco.prendi():
        log.info("Un altro controllo e' ancora in corso: salto questo giro")
        return 0
    try:
        archivio = Archivio(percorsi.db)
        try:
            aggiornato = aggiorna(percorsi.base, cfg, archivio)
        except Exception:
            log.exception("Aggiornamento automatico: errore imprevisto, continuo con la versione attuale")
            aggiornato = False
        finally:
            archivio.chiudi()
        if aggiornato:
            # il codice nuovo va eseguito in un processo nuovo
            argomenti = [sys.executable, str(percorsi.base / "radar.py"), "--controllo-interno",
                         "--cartella", str(percorsi.base)] + (["--senza-telegram"] if finto else [])
            return subprocess.run(argomenti, cwd=str(percorsi.base), creationflags=SENZA_FINESTRA).returncode
        esegui(cfg, percorsi, segreti, finto=finto)
        return 0
    finally:
        blocco.lascia()
