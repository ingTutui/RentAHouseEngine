"""Aggiornamento automatico dal repository: versione nuova presa, versione rotta scartata, modifiche locali salve."""
import os
import shutil
import subprocess
import time

import pytest

from motore.aggiorna import aggiorna
from motore.archivio import Archivio
from motore.config import carica_config
from motore.servizio import Blocco

from conftest import RADICE

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git non installato")
GIT = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=Prova", "-c", "user.email=prova@example.com"]


def git(cartella, *argomenti):
    r = subprocess.run(GIT + list(argomenti), cwd=cartella, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """Un 'GitHub' finto (repository bare), il PC sempre acceso e il PC dove si programma."""
    sorgente = tmp_path / "sorgente"
    shutil.copytree(RADICE, sorgente, ignore=shutil.ignore_patterns(".git", "stato", ".env", "__pycache__",
                                                                     ".pytest_cache", "config.local.toml"))
    git(sorgente, "init", "-q", "-b", "main")
    git(sorgente, "add", "-A")
    git(sorgente, "commit", "-q", "-m", "versione iniziale")
    remoto = tmp_path / "github.git"
    git(tmp_path, "clone", "-q", "--bare", str(sorgente), str(remoto))
    pc = tmp_path / "pc_acceso"
    dev = tmp_path / "pc_sviluppo"
    git(tmp_path, "clone", "-q", str(remoto), str(pc))
    git(tmp_path, "clone", "-q", str(remoto), str(dev))
    (pc / "stato").mkdir()
    return pc, dev


def pubblica(dev, file, testo, messaggio):
    (dev / file).write_text(testo, encoding="utf-8")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", messaggio)
    git(dev, "push", "-q", "origin", "main")
    return git(dev, "rev-parse", "HEAD")


def test_aggiornamento_buono_e_rotto(repo):
    pc, dev = repo
    arc = Archivio(pc / "stato" / "radar.db")
    cfg = carica_config(pc)
    assert aggiorna(pc, cfg, arc) is False                       # niente di nuovo
    nuova = pubblica(dev, "NOVITA.txt", "ciao\n", "una novita'")
    assert aggiorna(pc, cfg, arc) is True
    assert git(pc, "rev-parse", "HEAD") == nuova
    # una versione che non passa la verifica: si torna indietro e non la si riprova
    rotta = pubblica(dev, "config.toml", "questo non e' toml valido = = =\n", "config rotta")
    assert aggiorna(pc, cfg, arc) is False
    assert git(pc, "rev-parse", "HEAD") == nuova
    assert arc.get("aggiornamento_scartato") == rotta and "versione" in arc.get("aggiornamento_fallito")
    assert aggiorna(pc, cfg, arc) is False                       # stessa versione rotta: saltata subito
    # la correzione arriva: si aggiorna
    corretta = pubblica(dev, "config.toml", (RADICE / "config.toml").read_text(encoding="utf-8"), "config corretta")
    assert aggiorna(pc, cfg, arc) is True and git(pc, "rev-parse", "HEAD") == corretta
    assert arc.get("aggiornamento_fallito") == ""
    arc.chiudi()


def test_modifiche_locali_non_si_toccano(repo):
    pc, dev = repo
    arc = Archivio(pc / "stato" / "radar.db")
    cfg = carica_config(pc)
    (pc / "fonti" / "fonti_agenzie.csv").write_text("modificato a mano\n", encoding="utf-8")
    pubblica(dev, "fonti/fonti_agenzie.csv", "modificato su github\n", "fonti nuove")
    assert aggiorna(pc, cfg, arc) is False
    assert (pc / "fonti" / "fonti_agenzie.csv").read_text(encoding="utf-8") == "modificato a mano\n"
    cfg["aggiornamento"]["automatico"] = False
    assert aggiorna(pc, cfg, arc) is False
    arc.chiudi()


def test_blocco(tmp_path):
    a, b = Blocco(tmp_path / "radar.lock"), Blocco(tmp_path / "radar.lock")
    assert a.prendi() and not b.prendi()
    a.lascia()
    assert b.prendi()
    b.lascia()
    p = tmp_path / "radar.lock"
    p.write_text("999999 vecchio\n", encoding="utf-8")       # processo che non esiste piu'
    vecchio = time.time() - 4 * 3600
    os.utime(p, (vecchio, vecchio))
    c = Blocco(p)
    assert c.prendi()
    c.lascia()


def test_servizio_aggiorna_e_controlla(repo):
    import sys
    pc, dev = repo
    for nome, intestazione in (("fonti_portali.csv", "attiva;portale;comune;url;note\n"),
                               ("fonti_agenzie.csv", "attiva;agenzia;comune;url_affitti;js;pattern_link;note\n")):
        (pc / "fonti" / nome).write_text(intestazione, encoding="utf-8")   # niente internet nel test
    nuova = pubblica(dev, "NOVITA.txt", "ciao\n", "una novita'")
    r = subprocess.run([sys.executable, "radar.py", "--servizio", "--senza-telegram"], cwd=pc, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    diario = (pc / "stato" / "radar.log").read_text(encoding="utf-8")
    assert "Aggiornato da GitHub" in diario and "Fine controllo" in diario
    assert git(pc, "rev-parse", "HEAD") == nuova
    assert not (pc / "stato" / "radar.lock").exists()
