"""Le regole del progetto (CLAUDE.md) come controlli automatici: se una modifica ne rompe una per distrazione,
il test lo dice prima che finisca su main e, da li', sul PC sempre acceso."""
import ast
import importlib
import pkgutil
import re
import shutil
import subprocess
import sys

import pytest

import motore

from conftest import RADICE

CODICE = sorted([*(RADICE / "motore").glob("*.py"), RADICE / "radar.py"])
# libreria standard + requests, beautifulsoup4, lxml; Playwright e PyYAML solo facoltativi (import dentro una funzione)
LIBRERIE_AMMESSE = sys.stdlib_module_names | {"motore", "requests", "bs4", "lxml", "playwright", "yaml"}


def git(*argomenti: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argomenti], cwd=RADICE, capture_output=True, text=True, timeout=60)


@pytest.fixture(scope="module")
def tracciati() -> list[str]:
    """I file che git conserva (quelli che finiscono su GitHub)."""
    if shutil.which("git") is None or git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("serve un clone git")
    return git("ls-files").stdout.splitlines()


# --- segreti e file privati ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("percorso", [".env", "config.local.toml", "stato/radar.db", "stato/radar.log",
                                      "contesto/INDICE.md", "radar.db", "annunci_visti.csv"])
def test_i_file_privati_sono_ignorati_da_git(tracciati, percorso):
    assert git("check-ignore", "-q", percorso).returncode == 0, f"{percorso} deve stare in .gitignore"
    assert percorso not in tracciati


def test_nessun_file_privato_e_su_git(tracciati):
    privati = [f for f in tracciati if f.split("/")[0] in ("stato", "contesto") or f in (".env", "config.local.toml")]
    assert not privati, f"questi file non devono andare su GitHub: {privati}"


SEGRETI = {
    "chiave OpenRouter": re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    "token di un bot Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "chiave privata": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
ESEMPI_DI_CONFIGURAZIONE = re.compile(
    r"^[ \t]*(?:TELEGRAM_TOKEN|OPENROUTER_API_KEY|IDEALISTA_API_KEY|IDEALISTA_API_SECRET)[ \t]*=[ \t]*[^\s#]+",
    re.MULTILINE)


def test_nessun_segreto_nei_file_su_git(tracciati):
    trovati = []
    for nome in tracciati:
        if nome == "uv.lock" or nome.startswith("fonti/"):
            continue
        try:
            testo = (RADICE / nome).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        trovati += [f"{nome}: {cosa}" for cosa, rx in SEGRETI.items() if rx.search(testo)]
        if nome.startswith(".env") or nome.startswith("config"):    # i file d'esempio hanno i valori vuoti
            trovati += [f"{nome}: {m.group(0).strip()[:25]}..." for m in ESEMPI_DI_CONFIGURAZIONE.finditer(testo)]
    assert not trovati, "segreti nei file che vanno su GitHub (devono stare solo in .env): " + "; ".join(trovati)


# --- file che dipendono dal sistema -------------------------------------------------------------------------------


def test_i_file_bat_hanno_fine_riga_crlf_e_gli_sh_lf(tracciati):
    sbagliati = []
    for nome in tracciati:
        if nome.endswith(".bat"):
            dati = (RADICE / nome).read_bytes()
            if b"\r\n" not in dati or re.search(rb"(?<!\r)\n", dati):
                sbagliati.append(f"{nome}: serve CRLF")
        elif nome.endswith(".sh") and b"\r\n" in (RADICE / nome).read_bytes():
            sbagliati.append(f"{nome}: serve LF")
    assert not sbagliati, "; ".join(sbagliati) + " (non crearli con echo o printf: lo gestisce .gitattributes)"


# --- regole sul codice --------------------------------------------------------------------------------------------


def parse(percorso) -> ast.Module:
    return ast.parse(percorso.read_text(encoding="utf-8"), filename=str(percorso))


def test_il_codice_usa_solo_le_librerie_ammesse():
    # nuova libreria = chiedere prima, poi "uv add nome" (pyproject.toml e uv.lock insieme) e aggiornare questo elenco
    estranee = []
    for percorso in CODICE:
        for nodo in ast.walk(parse(percorso)):
            nomi = [a.name for a in nodo.names] if isinstance(nodo, ast.Import) else \
                [nodo.module or ""] if isinstance(nodo, ast.ImportFrom) and nodo.level == 0 else []
            estranee += [f"{percorso.name}: {n}" for n in nomi if n.split(".")[0] not in LIBRERIE_AMMESSE]
    assert not estranee, f"librerie non ammesse: {estranee}"


def test_la_verifica_del_certificato_tls_non_si_disattiva():
    trovati = []
    for percorso in CODICE:
        for nodo in ast.walk(parse(percorso)):
            if isinstance(nodo, ast.keyword) and nodo.arg == "verify" and \
                    isinstance(nodo.value, ast.Constant) and nodo.value.value is False:
                trovati.append(f"{percorso.name}:{nodo.value.lineno}: verify=False")
            if isinstance(nodo, ast.Attribute) and nodo.attr in ("_create_unverified_context", "CERT_NONE"):
                trovati.append(f"{percorso.name}:{nodo.lineno}: {nodo.attr}")
    assert not trovati, f"TLS disattivato (se una richiesta e' negata va segnalato, non aggirato): {trovati}"


def test_il_modello_dell_llm_non_e_scritto_nel_codice():
    # si sceglie in llm.toml (o nel file di LLM_CONFIG), mai nel codice
    modello = re.compile(r"^[a-z0-9-]+/(claude|gpt|gemini|llama|mistral|qwen|deepseek|grok)[\w.:-]*$", re.IGNORECASE)
    trovati = [f"{p.name}:{n.lineno}: {n.value}" for p in CODICE for n in ast.walk(parse(p))
               if isinstance(n, ast.Constant) and isinstance(n.value, str) and modello.match(n.value)]
    assert not trovati, f"nome di un modello nel codice (va in llm.toml): {trovati}"


def test_senza_future_annotations():
    # Python 3.14 ha le annotazioni di tipo native
    usati = [p.name for p in [*CODICE, *sorted((RADICE / "tests").glob("*.py"))]
             if any(isinstance(n, ast.ImportFrom) and n.module == "__future__" for n in ast.walk(parse(p)))]
    assert not usati, f"from __future__ nei file: {usati}"


def test_ogni_modulo_del_radar_si_importa():
    # verifica.py ne importa solo alcuni: un errore di sintassi in uno raro (idealista...) si scoprirebbe tardi
    nomi = [m.name for m in pkgutil.iter_modules(motore.__path__)]
    assert {"cli", "controllo", "aggiorna", "servizio", "idealista"} <= set(nomi)
    for nome in nomi:
        importlib.import_module(f"motore.{nome}")

