"""Aggiornamento automatico dal repository GitHub (ramo main) per il PC sempre acceso.

Prima di ogni controllo: git fetch; se su GitHub c'e' una versione nuova la scarica (solo fast-forward), allinea
le librerie con uv se pyproject.toml o uv.lock sono cambiati e lancia "radar.py --verifica". Se qualcosa non va
torna alla versione precedente (codice e librerie): se e' la verifica a fallire non riprova quella versione finche'
su GitHub non ne arriva un'altra; se sono le librerie (rete assente, uv mancante) riprova al giro dopo.
Le modifiche fatte a mano su questo PC non vengono mai cancellate: in quel caso l'aggiornamento si salta.

Questo processo deve restare leggero: niente requests, bs4 o lxml (ne' moduli che li importano subito), perche'
su Windows uv non riesce a sostituire i file di una libreria caricata da un programma in esecuzione.
tests/test_aggiorna.py lo controlla.
"""

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .archivio import adesso

log = logging.getLogger("radar")
SENZA_FINESTRA = 0x08000000 if os.name == "nt" else 0  # Windows: niente finestre nere che lampeggiano
# se cambia uno di questi file le librerie (.venv) vanno riallineate; la versione di Python no: la cambia l'utente
FILE_DELLE_LIBRERIE = {"pyproject.toml", "uv.lock"}


def _git(base: Path, *argomenti: str, timeout: int = 120) -> tuple[int, str]:
    # l'avvio automatico non ha nessuno davanti: se mancano le credenziali git deve fallire subito, non aspettare
    ambiente = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    return _esegui(["git", *argomenti], base, timeout, ambiente)


def _esegui(comando: list[str], base: Path, timeout: int, ambiente: dict | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(comando, cwd=str(base), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=SENZA_FINESTRA, env=ambiente)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, f"{type(ex).__name__}: {ex}"


def comando_uv() -> list[str] | None:
    """Come si lancia uv. Il PATH di cron e dell'attivita' pianificata puo' non averlo: si guarda anche nelle
    cartelle dove lo mettono gli installatori."""
    trovato = os.environ.get("UV") or shutil.which("uv")  # UV: la imposta "uv run"
    if trovato:
        return [trovato]
    nome = "uv.exe" if os.name == "nt" else "uv"
    casa = Path.home()
    for cartella in (casa / ".local" / "bin", casa / ".cargo" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if (cartella / nome).is_file():
            return [str(cartella / nome)]
    return None


def ambiente_di_uv(base: Path) -> bool:
    """True se il radar gira nell'ambiente .venv che 'uv sync' crea dentro la cartella del progetto."""
    return Path(sys.prefix).resolve() == (base / ".venv").resolve()


def sincronizza_librerie(base: Path) -> tuple[int, str]:
    """uv sync --locked: porta .venv alle versioni di uv.lock (Playwright resta, se c'era)."""
    if not ambiente_di_uv(base):
        return 1, "il radar non gira nell'ambiente .venv creato da 'uv sync' nella cartella del progetto"
    uv = comando_uv()
    if uv is None:
        return 1, "uv non trovato (vedi README: come installarlo)"
    comando = [*uv, "sync", "--locked", "--quiet"]
    if importlib.util.find_spec("playwright") is not None:
        comando += ["--extra", "browser"]
    return _esegui(comando, base, timeout=900)


def _python(base: Path, argomenti: list[str], timeout: int) -> tuple[int, str]:
    return _esegui([sys.executable, *argomenti], base, timeout)


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
    librerie_cambiate = bool(FILE_DELLE_LIBRERIE & set(cambiati.split()))
    motivo, dettaglio, definitivo = "", "", True
    if librerie_cambiate:
        c, out = sincronizza_librerie(base)
        if c != 0:
            motivo, dettaglio, definitivo = "librerie non allineate", out, False  # rete o uv: si riprova al giro dopo
    if not motivo:
        c, out = _python(base, ["radar.py", "--verifica"], timeout=300)
        if c != 0:
            motivo, dettaglio = "la verifica non e' passata", out
    if motivo:
        _git(base, "reset", "--keep", locale)
        if librerie_cambiate:
            sincronizza_librerie(base)  # anche le librerie tornano a quelle della versione precedente
        if definitivo:
            archivio.set("aggiornamento_scartato", nuovo)
        archivio.set("aggiornamento_fallito", f"{adesso()} versione {nuovo[:7]}: {motivo}: {dettaglio[-200:]}")
        log.error("Aggiornamento a %s annullato: %s. Resta la versione %s.\n%s", nuovo[:7], motivo, locale[:7],
                  dettaglio[-800:])
        return False
    archivio.set("aggiornamento_fallito", "")
    _, descrizione = _git(base, "log", "--oneline", "-1")
    log.info("Aggiornato da GitHub: %s -> %s (%s)", locale[:7], nuovo[:7], descrizione[:80])
    return True
