"""Client OpenRouter per i compiti in cui serve un LLM (dati dalla pagina dell'annuncio, doppioni dubbi).

La chiave sta nel file .env (OPENROUTER_API_KEY). Il modello si sceglie nel file indicato da
[llm] file_modello in config.toml (di solito llm.toml) oppure con OPENROUTER_MODEL nel .env.
Se manca qualcosa, o se si superano i limiti di spesa, il radar lavora come prima, senza LLM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import requests

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

log = logging.getLogger("radar")
URL_BASE = "https://openrouter.ai/api/v1"
REPO = "https://github.com/ingTutui/RentAHouseEngine"
MODI_JSON = ("schema", "oggetto", "testo")   # json_schema rigoroso -> json_object -> solo istruzioni


def _primo(d: dict, *chiavi: str, default: Any = None) -> Any:
    for k in chiavi:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def carica_modello(base: Path, cfg: dict, segreti: Dict[str, str]) -> dict:
    """Legge il file di configurazione del modello. Accetta TOML o JSON (YAML se e' installato PyYAML),
    con chiavi in italiano (modello, temperatura...) o in inglese (model, temperature...)."""
    nome = segreti.get("LLM_CONFIG") or cfg.get("llm", {}).get("file_modello") or "llm.toml"
    p = Path(nome)
    if not p.is_absolute():
        p = Path(base) / p
    dati: dict = {}
    if p.exists():
        try:
            if p.suffix.lower() == ".json":
                dati = json.loads(p.read_text(encoding="utf-8-sig"))
            elif p.suffix.lower() in (".yaml", ".yml"):
                import yaml  # type: ignore
                dati = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
            else:
                with open(p, "rb") as f:
                    dati = tomllib.load(f)
        except Exception as ex:  # file scritto male: meglio dirlo che fermare il radar
            log.error("Non riesco a leggere %s (%s): LLM disattivato", p.name, ex)
            dati = {}
    for sezione in ("llm", "openrouter", "modello", "model"):
        if isinstance(dati.get(sezione), dict):
            dati = {**dati, **dati[sezione]}
    modelli = _primo(dati, "modelli", "models", default=[])
    if isinstance(modelli, str):
        modelli = [modelli]
    modello = segreti.get("OPENROUTER_MODEL") or _primo(dati, "modello", "model", "default_model", "nome")
    if not isinstance(modello, str):
        modello = ""
    if not modello and modelli:
        modello = modelli[0]
    riserva = _primo(dati, "modelli_riserva", "fallback_models", "fallbacks", default=[]) or \
        [m for m in modelli if m != modello]
    if isinstance(riserva, str):
        riserva = [riserva]
    return {
        "file": str(p) if p.exists() else "",
        "modello": modello.strip(),
        "riserva": [m for m in riserva if isinstance(m, str) and m.strip() and m != modello],
        "temperatura": float(_primo(dati, "temperatura", "temperature", default=0)),
        "max_token": int(_primo(dati, "max_token", "max_tokens", "max_output_tokens", default=1500)),
        "timeout": float(_primo(dati, "timeout_sec", "timeout", default=60)),
        "provider": _primo(dati, "provider", default=None),
        "reasoning": _primo(dati, "reasoning", "ragionamento", default=None),
        "extra": _primo(dati, "extra", "parametri", default={}) or {},
    }


def _contenuto(messaggio: dict) -> str:
    c = messaggio.get("content")
    if isinstance(c, list):  # alcuni fornitori rispondono a pezzi
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def leggi_json(testo: str) -> Optional[dict]:
    t = (testo or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.S)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except ValueError:
        pass
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            v = json.loads(t[i:j + 1])
            return v if isinstance(v, dict) else None
        except ValueError:
            return None
    return None


class ClienteLLM:
    def __init__(self, cfg: dict, segreti: Dict[str, str], archivio, base: Path):
        self.cfg = cfg.get("llm", {})
        self.archivio = archivio
        self.conf = carica_modello(base, cfg, segreti)
        self.chiave = (segreti.get("OPENROUTER_API_KEY") or "").strip()
        self.url = (segreti.get("OPENROUTER_BASE_URL") or URL_BASE).rstrip("/")
        self.chiamate = 0
        self.motivo = ""
        if not self.cfg.get("usa", True):
            self.motivo = "disattivato in config.toml ([llm] usa = false)"
        elif not self.chiave:
            self.motivo = "manca OPENROUTER_API_KEY nel file .env"
        elif not self.conf["modello"]:
            self.motivo = "manca il modello: scrivilo in llm.toml (modello = \"...\") o in OPENROUTER_MODEL"
        self.attivo = not self.motivo
        chiave_modo = f"llm_modo:{self.conf['modello']}"
        self._chiave_modo = chiave_modo
        salvato = archivio.get(chiave_modo, "") if archivio is not None else ""
        self.modo = salvato if salvato in MODI_JSON else "schema"

    # ------------------------------------------------------------------ limiti
    def disponibile(self) -> bool:
        if not self.attivo:
            return False
        if self.chiamate >= int(self.cfg.get("max_chiamate_per_controllo", 40)):
            return False
        if self.archivio is not None:
            n, costo = self.archivio.uso_llm(date.today().isoformat())
            if n >= int(self.cfg.get("max_chiamate_al_giorno", 300)):
                return False
            limite = float(self.cfg.get("max_spesa_giornaliera_usd", 0) or 0)
            if limite and costo >= limite:
                return False
        return True

    def stato(self) -> str:
        if not self.attivo:
            return f"LLM non attivo: {self.motivo}"
        return f"LLM attivo: {self.conf['modello']}" + (f" (+{len(self.conf['riserva'])} di riserva)"
                                                        if self.conf["riserva"] else "")

    # ------------------------------------------------------------------ chiamata
    def _corpo(self, modo: str, sistema: str, utente: str, schema: dict, nome: str) -> dict:
        c = self.conf
        corpo: Dict[str, Any] = {
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            "temperature": c["temperatura"],
            "max_tokens": c["max_token"],
            "usage": {"include": True},
        }
        if c["riserva"]:
            corpo["models"] = [c["modello"]] + c["riserva"]
        else:
            corpo["model"] = c["modello"]
        provider = dict(c["provider"]) if isinstance(c["provider"], dict) else {}
        if modo == "schema":
            corpo["response_format"] = {"type": "json_schema",
                                        "json_schema": {"name": nome, "strict": True, "schema": schema}}
            provider["require_parameters"] = True
        elif modo == "oggetto":
            corpo["response_format"] = {"type": "json_object"}
        if provider:
            corpo["provider"] = provider
        if c["reasoning"]:
            corpo["reasoning"] = c["reasoning"]
        if isinstance(c["extra"], dict):
            corpo.update(c["extra"])
        return corpo

    def chiedi_json(self, compito: str, sistema: str, utente: str, schema: dict) -> Optional[dict]:
        """Risposta JSON del modello, oppure None (LLM non disponibile o risposta non valida)."""
        if not self.disponibile():
            return None
        chiave = hashlib.sha256(f"{self.conf['modello']}|{compito}|{sistema}|{utente}".encode("utf-8")).hexdigest()
        if self.archivio is not None:
            salvata = self.archivio.cache_llm(chiave)
            if salvata:
                v = leggi_json(salvata)
                if v is not None:
                    return v
        sistema_completo = sistema + "\nRispondi solo con un oggetto JSON valido, senza testo prima o dopo."
        indice = MODI_JSON.index(self.modo)
        tentativi_rete = 0
        while indice < len(MODI_JSON):
            modo = MODI_JSON[indice]
            corpo = self._corpo(modo, sistema_completo, utente, schema, compito)
            try:
                self.chiamate += 1
                r = requests.post(self.url + "/chat/completions", json=corpo, timeout=self.conf["timeout"],
                                  headers={"Authorization": f"Bearer {self.chiave}", "HTTP-Referer": REPO,
                                           "X-Title": "RentAHouseEngine"})
            except requests.RequestException as ex:
                tentativi_rete += 1
                if tentativi_rete >= 2:
                    log.warning("OpenRouter non raggiungibile (%s)", type(ex).__name__)
                    return None
                time.sleep(3)
                continue
            if r.status_code in (401, 403):
                self.attivo, self.motivo = False, f"chiave OpenRouter rifiutata (HTTP {r.status_code})"
                log.error("OpenRouter: %s. Controlla OPENROUTER_API_KEY nel file .env", self.motivo)
                return None
            if r.status_code == 402:
                self.attivo, self.motivo = False, "credito OpenRouter esaurito"
                log.error("OpenRouter: credito esaurito, per oggi il radar lavora senza LLM")
                return None
            if r.status_code == 429 or r.status_code >= 500:
                tentativi_rete += 1
                if tentativi_rete >= 2:
                    log.warning("OpenRouter: HTTP %s, riprovo al prossimo controllo", r.status_code)
                    return None
                time.sleep(min(30, float(r.headers.get("Retry-After", "5") or 5)))
                continue
            if r.status_code >= 400:
                errore = r.text[:300]
                if modo != "testo" and re.search(r"response_format|json_schema|structured|parameter|endpoint",
                                                 errore, re.I):
                    indice += 1          # il modello non supporta questo modo: prova quello successivo
                    continue
                log.error("OpenRouter: HTTP %s %s", r.status_code, errore)
                if r.status_code in (400, 404):
                    self.attivo, self.motivo = False, f"richiesta rifiutata (HTTP {r.status_code}): controlla il modello"
                return None
            try:
                dati = r.json()
            except ValueError:
                log.warning("OpenRouter: risposta non leggibile")
                return None
            if dati.get("error"):
                log.warning("OpenRouter: %s", str(dati["error"])[:200])
                return None
            self._registra(dati)
            if modo != self.modo:
                self.modo = modo
                if self.archivio is not None:
                    self.archivio.set(self._chiave_modo, modo)
            scelta = (dati.get("choices") or [{}])[0]
            testo = _contenuto(scelta.get("message") or {})
            v = leggi_json(testo)
            if v is None:
                if scelta.get("finish_reason") == "length":
                    log.warning("OpenRouter: risposta troncata, aumenta max_token nel file del modello")
                else:
                    log.warning("OpenRouter: risposta senza JSON valido")
                return None
            if self.archivio is not None:
                self.archivio.salva_cache_llm(chiave, json.dumps(v, ensure_ascii=False))
            return v
        return None

    def _registra(self, dati: dict) -> None:
        if self.archivio is None:
            return
        u = dati.get("usage") or {}
        try:
            costo = float(u.get("cost") or 0)
        except (TypeError, ValueError):
            costo = 0.0
        self.archivio.registra_llm(date.today().isoformat(), int(u.get("prompt_tokens") or 0),
                                   int(u.get("completion_tokens") or 0), costo)


def prova(cliente: ClienteLLM) -> str:
    """Una richiesta minima per controllare chiave e modello (comando --prova-llm)."""
    if not cliente.attivo:
        return cliente.stato()
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "saluto": {"type": "string"}},
              "required": ["ok", "saluto"], "additionalProperties": False}
    inizio = time.monotonic()
    v = cliente.chiedi_json("prova", "Sei un assistente di prova.",
                            f"Rispondi con ok=true e un saluto in italiano di tre parole. ({time.time():.0f})", schema)
    secondi = time.monotonic() - inizio
    if v is None:
        return f"Il modello {cliente.conf['modello']} non ha risposto correttamente ({cliente.motivo or 'vedi radar.log'})"
    return (f"OK: {cliente.conf['modello']} ha risposto in {secondi:.1f} s (modo JSON: {cliente.modo}). "
            f"Risposta: {json.dumps(v, ensure_ascii=False)}")
