"""Aggiornamento automatico dal repository GitHub (ramo main) per il PC sempre acceso.

Prima di ogni controllo: git fetch; se su GitHub c'e' una versione nuova la scarica (solo fast-forward),
installa le dipendenze se requirements.txt e' cambiato e lancia "radar.py --verifica". Se la verifica
fallisce torna alla versione precedente e non riprova quella versione finche' su GitHub non ne arriva un'altra.
Le modifiche fatte a mano su questo PC non vengono mai cancellate: in quel caso l'aggiornamento si salta.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .archivio import adesso

log = logging.getLogger("radar")
SENZA_FINESTRA = 0x08000000 if os.name == "nt" else 0  # Windows: niente finestre nere che lampeggiano


def _git(base: Path, *argomenti: str, timeout: int = 120) -> tuple[int, str]:
    # l'avvio automatico non ha nessuno davanti: se mancano le credenziali git deve fallire subito, non aspettare
    ambiente = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        r = subprocess.run(["git", *argomenti], cwd=str(base), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=SENZA_FINESTRA, env=ambiente)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, f"{type(ex).__name__}: {ex}"


def _python(base: Path, argomenti: list[str], timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, *argomenti], cwd=str(base), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=SENZA_FINESTRA)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, f"{type(ex).__name__}: {ex}"


def aggiorna(base: Path, cfg: dict, archivio) -> bool:
    """True se il codice e' stato aggiornato (il controllo va poi lanciato con il codice nuovo)."""
    a = cfg.get("aggiornamento", {})
    if not a.get("automatico", True):
        return False
    if not (base / ".git").exists():
        log.debug("Aggiornamento automatico: la cartella non e' un clone git")
        return False
    if not shutil.which("git"):
        log.warning("Aggiornamento automatico: git non e' installato")
        return False
    remoto, ramo = a.get("remoto", "origin"), a.get("ramo", "main")
    codice, uscita = _git(base, "fetch", "--quiet", remoto, ramo)
    if codice != 0:
        log.info("Aggiornamento: GitHub non raggiungibile (%s)", uscita[:200])
        return False
    _, locale = _git(base, "rev-parse", "HEAD")
    _, nuovo = _git(base, "rev-parse", f"{remoto}/{ramo}")
    if not nuovo or locale == nuovo:
        return False
    if archivio.get("aggiornamento_scartato") == nuovo:
        return False  # questa versione ha gia' fallito la verifica
    _, attuale = _git(base, "rev-parse", "--abbrev-ref", "HEAD")
    if attuale != ramo:
        log.info("Aggiornamento saltato: questa cartella e' sul ramo %s, non su %s", attuale, ramo)
        return False
    codice, _ = _git(base, "merge-base", "--is-ancestor", "HEAD", f"{remoto}/{ramo}")
    if codice != 0:
        log.warning("Aggiornamento saltato: qui ci sono commit che non sono su GitHub")
        return False
    codice, uscita = _git(base, "merge", "--ff-only", "--quiet", f"{remoto}/{ramo}")
    if codice != 0:
        log.warning("Aggiornamento saltato: modifiche locali in conflitto (%s)", uscita[:200])
        return False
    _, cambiati = _git(base, "diff", "--name-only", locale, nuovo)
    if "requirements.txt" in cambiati.split():
        c, out = _python(base, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r",
                                "requirements.txt"], timeout=900)
        if c != 0:
            log.warning("Aggiornamento: installazione delle dipendenze non riuscita (%s)", out[-300:])
    c, out = _python(base, ["radar.py", "--verifica"], timeout=300)
    if c != 0:
        _git(base, "reset", "--keep", locale)
        archivio.set("aggiornamento_scartato", nuovo)
        archivio.set("aggiornamento_fallito", f"{adesso()} versione {nuovo[:7]}: {out[-200:]}")
        log.error("Aggiornamento a %s annullato: la verifica non e' passata. Resta la versione %s.\n%s",
                  nuovo[:7], locale[:7], out[-800:])
        return False
    archivio.set("aggiornamento_fallito", "")
    _, descrizione = _git(base, "log", "--oneline", "-1")
    log.info("Aggiornato da GitHub: %s -> %s (%s)", locale[:7], nuovo[:7], descrizione[:80])
    return True
