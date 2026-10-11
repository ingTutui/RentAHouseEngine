"""Strumenti comuni dei test: un piccolo server HTTP locale che fa da sito di agenzia, da Telegram e da
OpenRouter, e una cartella del radar temporanea con le sue fonti."""

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


@pytest.fixture
def server():
    s = Server()
    yield s
    s.chiudi()


@pytest.fixture
def cartella(tmp_path, server):
    """Cartella del radar con config.toml del repository, fonti vuote e .env che punta ai servizi finti."""
    shutil.copy(RADICE / "config.toml", tmp_path / "config.toml")
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


def aggiungi_agenzia(cartella: Path, nome: str, comune: str, url: str, pattern: str = "") -> None:
    with open(cartella / "fonti" / "fonti_agenzie.csv", "a", encoding="utf-8") as f:
        f.write(f"sì;{nome};{comune};{url};no;{pattern};\n")


def scrivi_locale(cartella: Path, testo: str) -> None:
    (cartella / "config.local.toml").write_text(testo, encoding="utf-8")
