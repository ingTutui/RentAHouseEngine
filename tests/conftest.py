"""Strumenti comuni dei test: un piccolo server HTTP locale che fa da sito di agenzia, da Telegram e da
OpenRouter, e una cartella del radar temporanea con le sue fonti.

I test del comportamento (filtri, notifiche, doppioni, giri completi) usano una configurazione FISSA
(config_di_prova), non config.toml: cambiare prezzo, metratura, orari o comuni in config.toml non li rompe.
I test che guardano il config.toml vero stanno in test_configurazione.py e leggono i valori dal file."""

import copy
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import pytest

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))

from motore.config import DEFAULT_CFG, Percorsi, carica_config, carica_segreti  # noqa: E402
from motore import rete  # noqa: E402
from motore.controllo import esegui  # noqa: E402

FILTRI_PROVA = {
    "prezzo_min": 0, "prezzo_max": 700, "mq_min": 55, "mq_max": 0, "locali_min": 0,
    "escludi_stanze": True, "dati_mancanti": "invia", "fuori_zona": "scarta",
    "comuni_area": ["Modena", "Formigine", "Sassuolo", "Spilamberto", "Soliera", "Castelnuovo Rangone",
                    "Fiorano Modenese"],
    "zone_fuori_area": ["Limidi", "Carpi", "Correggio", "Bomporto"],
    "frazioni_area": {"Carpi": ["Gargallo"], "Reggio nell'Emilia": ["Bagno", "Marmirolo"]},
}


def config_di_prova() -> dict:
    """Configurazione fissa dei test (una copia nuova a ogni chiamata, si puo' modificare)."""
    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg["filtri"].update(copy.deepcopy(FILTRI_PROVA))
    cfg["notifiche"].update(silenzio_dalle="22:00", silenzio_alle="08:00")
    return cfg


def _valore_toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_valore_toml(x) for x in v) + "]"
    raise TypeError(f"valore non scrivibile in TOML: {v!r}")


def a_toml(cfg: dict) -> str:
    """Scrive una configurazione (sezioni con valori semplici e una sottosezione) come file TOML."""
    righe: list[str] = []
    for sezione, valori in cfg.items():
        if sezione.startswith("_") or not isinstance(valori, dict):
            continue
        righe.append(f"[{sezione}]")
        righe += [f"{json.dumps(k)} = {_valore_toml(v)}" for k, v in valori.items() if not isinstance(v, dict)]
        for k, sotto in valori.items():
            if isinstance(sotto, dict) and sotto:
                righe.append(f"[{sezione}.{k}]")
                righe += [f"{json.dumps(nome, ensure_ascii=False)} = {_valore_toml(v)}" for nome, v in sotto.items()]
        righe.append("")
    return "\n".join(righe)


class Server:
    """Server locale: pagine statiche (modificabili durante il test) e gestori per i percorsi dinamici."""

    def __init__(self):
        self.pagine: dict[str, tuple[int, str, str]] = {}
        self.gestori: dict[str, Callable[[str, dict, bytes], tuple[int, str, str]]] = {}
        self.richieste: list[tuple[str, str]] = []
        server = self

        class Gestore(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silenzio
                pass

            def _rispondi(self, metodo: str, corpo: bytes) -> None:
                percorso = urlparse(self.path).path
                server.richieste.append((metodo, self.path))
                for prefisso, f in server.gestori.items():
                    if percorso.startswith(prefisso):
                        stato, tipo, testo = f(self.path, dict(self.headers), corpo)
                        break
                else:
                    stato, tipo, testo = server.pagine.get(self.path) or server.pagine.get(percorso) or \
                        (404, "text/plain", "non trovato")
                dati = testo.encode("utf-8")
                self.send_response(stato)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(dati)))
                self.end_headers()
                self.wfile.write(dati)

            def do_GET(self):
                self._rispondi("GET", b"")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self._rispondi("POST", self.rfile.read(n))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Gestore)
        self.porta = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, percorso: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.porta}{percorso}"

    def pagina(self, percorso: str, html: str, stato: int = 200, tipo: str = "text/html; charset=utf-8") -> None:
        self.pagine[percorso] = (stato, tipo, html)

    def chiudi(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class FintoTelegram:
    def __init__(self, server: Server, rifiuta: bool = False):
        self.messaggi: list[str] = []
        self.campi: list[dict] = []          # tutti i campi di ogni richiesta sendMessage
        self.rifiuta = rifiuta
        self.respingi: Callable[[dict], str] | None = None   # campi -> descrizione di un errore 400 ("" = accetta)
        server.gestori["/botTOKEN"] = self

    def __call__(self, path: str, headers: dict, corpo: bytes):
        if self.rifiuta:
            return 500, "application/json", json.dumps({"ok": False, "description": "errore finto"})
        dati = {k: v[0] for k, v in parse_qs(corpo.decode("utf-8")).items()}
        descrizione = self.respingi(dati) if self.respingi else ""
        if descrizione:
            return 400, "application/json", json.dumps({"ok": False, "error_code": 400, "description": descrizione})
        self.campi.append(dati)
        self.messaggi.append(dati.get("text", ""))
        return 200, "application/json", json.dumps({"ok": True, "result": {"message_id": len(self.messaggi)}})


class FintoOpenRouter:
    """Risponde alle richieste chat/completions con la funzione 'risposta(corpo) -> dict' scelta dal test."""

    def __init__(self, server: Server, risposta: Callable[[dict], dict] | None = None):
        self.corpi: list[dict] = []
        self.risposta = risposta or (lambda corpo: {"ok": True, "saluto": "ciao a tutti"})
        self.errore: tuple[int, str] | None = None
        self.rifiuta_schema = False
        self.rifiuto = (400, "response_format json_schema not supported")   # codice e messaggio quando rifiuta
        server.gestori["/api/v1/chat/completions"] = self

    def __call__(self, path: str, headers: dict, corpo: bytes):
        c = json.loads(corpo.decode("utf-8"))
        self.corpi.append({"headers": headers, **c})
        if self.errore:
            return self.errore[0], "application/json", self.errore[1]
        if self.rifiuta_schema and (c.get("response_format") or {}).get("type") == "json_schema":
            codice, messaggio = self.rifiuto
            return codice, "application/json", json.dumps({"error": {"code": codice, "message": messaggio}})
        contenuto = json.dumps(self.risposta(c), ensure_ascii=False)
        return 200, "application/json", json.dumps({
            "id": "gen-1", "model": c.get("model") or (c.get("models") or ["?"])[0],
            "choices": [{"message": {"role": "assistant", "content": contenuto}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.0002}})


@pytest.fixture(autouse=True)
def senza_attese_casuali(monkeypatch):
    """rete.py aggiunge alla pausa tra due richieste allo stesso sito fino a 1,5 secondi a caso: nei test non serve
    (e rallenterebbe i giri completi di minuti). La pausa vera si prova in test_rete."""
    monkeypatch.setattr(rete.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def server():
    s = Server()
    yield s
    s.chiudi()


def _cartella_del_radar(tmp_path, server) -> Path:
    (tmp_path / "fonti").mkdir()
    (tmp_path / "fonti" / "fonti_portali.csv").write_text("attiva;portale;comune;url;note\n", encoding="utf-8")
    (tmp_path / "fonti" / "fonti_agenzie.csv").write_text(
        "attiva;agenzia;comune;url_affitti;js;pattern_link;note\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"TELEGRAM_TOKEN=TOKEN\nTELEGRAM_CHAT_ID=42\nTELEGRAM_API_URL={server.url('')}\n"
        f"OPENROUTER_BASE_URL={server.url('/api/v1')}\n", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(
        "[esecuzione]\npausa_stesso_sito_sec = 0\ntimeout_sec = 5\nusa_browser_per_js = false\n"
        "[notifiche]\nsilenzio_dalle = \"\"\nsilenzio_alle = \"\"\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def cartella(tmp_path, server):
    """Cartella del radar con la configurazione di prova (fissa), fonti vuote e .env che punta ai servizi finti."""
    (tmp_path / "config.toml").write_text(a_toml(config_di_prova()), encoding="utf-8")
    return _cartella_del_radar(tmp_path, server)


@pytest.fixture
def cartella_vera(tmp_path, server):
    """Come 'cartella' ma con il config.toml del repository: serve ai test che controllano proprio quel file."""
    shutil.copy(RADICE / "config.toml", tmp_path / "config.toml")
    return _cartella_del_radar(tmp_path, server)


def lista(prefisso: str, annunci: list[tuple[str, str, str]]) -> str:
    """Pagina di elenco di un'agenzia finta: annunci = [(slug, titolo, riga di dati)]."""
    schede = "".join(f'<article class="card"><h3><a href="/{prefisso}/immobile/{s}/">{t}</a></h3><p>{info}</p></article>'
                     for s, t, info in annunci)
    return (f"<html><body><nav><a href='/{prefisso}/'>Home</a> <a href='/{prefisso}/contatti/'>Contatti</a></nav>"
            f"<div class='lista'>{schede}</div></body></html>")


def giro(cartella: Path, **k) -> int:
    """Un controllo completo del radar nella cartella; restituisce quanti annunci nuovi ha messo in coda."""
    cfg = carica_config(cartella)
    p = Percorsi(cartella)
    return esegui(cfg, p, carica_segreti(p, cfg), **k)


def aggiungi_agenzia(cartella: Path, nome: str, comune: str, url: str, pattern: str = "") -> None:
    with open(cartella / "fonti" / "fonti_agenzie.csv", "a", encoding="utf-8") as f:
        f.write(f"sì;{nome};{comune};{url};no;{pattern};\n")


def scrivi_locale(cartella: Path, testo: str) -> None:
    (cartella / "config.local.toml").write_text(testo, encoding="utf-8")
