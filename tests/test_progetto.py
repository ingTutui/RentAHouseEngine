"""Il progetto e' gestito da uv: pyproject.toml, uv.lock e la versione di Python devono restare coerenti."""
import shutil
import subprocess
import tomllib

import pytest

from motore import __version__

from conftest import RADICE

PROGETTO = tomllib.loads((RADICE / "pyproject.toml").read_text(encoding="utf-8"))


def test_versione_del_programma_e_quella_del_progetto():
    assert PROGETTO["project"]["version"] == __version__


def test_python_richiesto_e_lo_stesso_ovunque():
    assert PROGETTO["project"]["requires-python"] == ">=3.14"
    assert (RADICE / ".python-version").read_text(encoding="utf-8").strip() == "3.14"   # uv sceglie questo Python
    assert "PYTHON_MINIMO = (3, 14)" in (RADICE / "radar.py").read_text(encoding="utf-8")


def test_non_ci_sono_piu_i_file_requirements():
    # le librerie stanno solo in pyproject.toml e uv.lock: due elenchi diverrebbero presto incoerenti
    assert not list(RADICE.glob("requirements*.txt"))


def test_il_progetto_e_un_programma_non_una_libreria():
    assert PROGETTO["tool"]["uv"]["package"] is False
    assert {"requests", "beautifulsoup4", "lxml"} <= {d.split(">")[0] for d in PROGETTO["project"]["dependencies"]}


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv non installato")
def test_uv_lock_e_aggiornato():
    r = subprocess.run(["uv", "lock", "--check", "--offline"], cwd=RADICE, capture_output=True, text=True, timeout=120)
    # se fallisce: lancia "uv lock" e committa uv.lock insieme a pyproject.toml
    assert r.returncode == 0, r.stdout + r.stderr
