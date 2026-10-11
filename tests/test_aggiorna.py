"""Aggiornamento automatico dal repository: versione nuova presa, versione rotta scartata, modifiche locali salve,
librerie riallineate con uv."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from motore.aggiorna import aggiorna, comando_uv, sincronizza_librerie
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
    shutil.copytree(RADICE, sorgente, ignore=shutil.ignore_patterns(".git", ".venv", "stato", ".env", "__pycache__",
                                                                     ".pytest_cache", ".ruff_cache", "contesto",
                                                                     "config.local.toml"))
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


# --- librerie con uv ---------------------------------------------------------------------------------------------


class FalsoUv:
    """Un 'uv' finto: scrive come e' stato chiamato e esce con il codice scelto dal test."""

    def __init__(self, cartella):
        self.registro = cartella / "chiamate_uv.txt"
        self.esito = cartella / "esito_uv.txt"
        self.script = cartella / "falso_uv.py"
        self.script.write_text(
            "import pathlib, sys\n"
            f"registro, esito = pathlib.Path({str(self.registro)!r}), pathlib.Path({str(self.esito)!r})\n"
            "with registro.open('a', encoding='utf-8') as f:\n"
            "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(esito.read_text()) if esito.exists() else 0)\n", encoding="utf-8")

    def fallisce(self, si: bool = True) -> None:
        self.esito.write_text("1" if si else "0", encoding="utf-8")

    @property
    def chiamate(self) -> list[str]:
        return self.registro.read_text(encoding="utf-8").splitlines() if self.registro.exists() else []


@pytest.fixture
def falso_uv(tmp_path, monkeypatch):
    uv = FalsoUv(tmp_path)
    monkeypatch.setattr("motore.aggiorna.comando_uv", lambda: [sys.executable, str(uv.script)])
    monkeypatch.setattr("motore.aggiorna.ambiente_di_uv", lambda base: True)   # i test non girano in pc_acceso/.venv
    return uv


def pubblica_lock(dev, messaggio="librerie nuove"):
    return pubblica(dev, "uv.lock", (dev / "uv.lock").read_text(encoding="utf-8") + "# ritocco\n", messaggio)


def test_le_librerie_si_riallineano_solo_se_cambiano_pyproject_o_uv_lock(repo, falso_uv):
    pc, dev = repo
    arc, cfg = Archivio(pc / "stato" / "radar.db"), carica_config(pc)
    pubblica(dev, "NOVITA.txt", "ciao\n", "una novita'")
    assert aggiorna(pc, cfg, arc) is True and falso_uv.chiamate == []
    nuova = pubblica_lock(dev)
    assert aggiorna(pc, cfg, arc) is True and git(pc, "rev-parse", "HEAD") == nuova
    assert len(falso_uv.chiamate) == 1 and falso_uv.chiamate[0].startswith("sync --locked --quiet")
    arc.chiudi()


def test_librerie_che_non_si_allineano_annullano_l_aggiornamento_ma_si_riprova(repo, falso_uv):
    pc, dev = repo
    arc, cfg = Archivio(pc / "stato" / "radar.db"), carica_config(pc)
    prima = git(pc, "rev-parse", "HEAD")
    falso_uv.fallisce()
    nuova = pubblica_lock(dev)
    assert aggiorna(pc, cfg, arc) is False
    assert git(pc, "rev-parse", "HEAD") == prima                  # il codice torna indietro: o tutto o niente
    assert len(falso_uv.chiamate) == 2                            # sync verso la versione nuova, poi verso la vecchia
    assert not arc.get("aggiornamento_scartato")                  # non e' una versione rotta
    assert "librerie non allineate" in arc.get("aggiornamento_fallito")
    falso_uv.fallisce(False)                                      # tornata la rete: al giro dopo si riprova
    assert aggiorna(pc, cfg, arc) is True and git(pc, "rev-parse", "HEAD") == nuova
    assert arc.get("aggiornamento_fallito") == ""
    arc.chiudi()


def test_versione_rotta_con_librerie_nuove_riporta_indietro_anche_le_librerie(repo, falso_uv):
    pc, dev = repo
    arc, cfg = Archivio(pc / "stato" / "radar.db"), carica_config(pc)
    prima = git(pc, "rev-parse", "HEAD")
    pubblica_lock(dev)
    rotta = pubblica(dev, "config.toml", "questo non e' toml valido = = =\n", "config rotta")
    assert aggiorna(pc, cfg, arc) is False
    assert git(pc, "rev-parse", "HEAD") == prima
    assert arc.get("aggiornamento_scartato") == rotta             # la verifica e' fallita: questa versione non si riprova
    assert len(falso_uv.chiamate) == 2                            # librerie nuove, poi di nuovo quelle vecchie
    arc.chiudi()


def test_playwright_resta_installato_se_c_era(tmp_path, falso_uv, monkeypatch):
    originale = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda nome, *a: object() if nome == "playwright" else originale(nome, *a))
    assert sincronizza_librerie(tmp_path)[0] == 0
    assert falso_uv.chiamate == ["sync --locked --quiet --extra browser"]


def test_senza_l_ambiente_di_uv_le_librerie_non_si_toccano(tmp_path):
    codice, messaggio = sincronizza_librerie(tmp_path)           # tmp_path non ha un .venv: non e' l'ambiente in uso
    assert codice == 1 and ".venv" in messaggio


def test_uv_si_trova_anche_fuori_dal_path(tmp_path, monkeypatch):
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(shutil, "which", lambda nome: None)       # cron e attivita' pianificata hanno un PATH scarno
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    nome = "uv.exe" if os.name == "nt" else "uv"
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / nome).write_text("", encoding="utf-8")
    assert comando_uv() == [str(tmp_path / ".local" / "bin" / nome)]
    monkeypatch.setenv("UV", "/percorso/di/uv")                   # la imposta 'uv run'
    assert comando_uv() == ["/percorso/di/uv"]


def test_chi_aggiorna_non_ha_caricato_le_librerie_esterne(cartella, tmp_path):
    """Su Windows uv non puo' sostituire una libreria gia' caricata: il processo che aggiorna deve restare leggero."""
    uscita = tmp_path / "caricati.json"
    codice = (
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        "import motore.servizio as s\n"
        "def finto(base, cfg, archivio):\n"
        "    pesanti = ('requests', 'bs4', 'lxml', 'urllib3', 'charset_normalizer', 'playwright')\n"
        "    json.dump([m for m in pesanti if m in sys.modules], open(sys.argv[1], 'w'))\n"
        "    raise SystemExit(0)\n"
        "s.aggiorna = finto\n"
        "from motore.cli import main\n"
        "main(['--cartella', sys.argv[2], '--servizio', '--senza-telegram'])\n")
    r = subprocess.run([sys.executable, "-c", codice, str(uscita), str(cartella)], cwd=RADICE, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(uscita.read_text(encoding="utf-8")) == []
