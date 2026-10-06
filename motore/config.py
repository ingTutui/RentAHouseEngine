"""Impostazioni del radar.

- config.toml        impostazioni condivise (sono nel repository: valgono su tutti i PC)
- config.local.toml  facoltativo, solo su questo PC: sovrascrive config.toml sezione per sezione
- .env               segreti (token Telegram, chiave OpenRouter...): mai nel repository
"""
from __future__ import annotations

import copy
import os
import re
import shutil
from pathlib import Path
from typing import Dict

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.9 / 3.10: pip install tomli
    import tomli as tomllib  # type: ignore

DEFAULT_CFG: dict = {
    "esecuzione": {
        "intervallo_minuti": 30,
        "pausa_stesso_sito_sec": 4,
        "timeout_sec": 25,
        "usa_browser_per_js": True,
        "pagine_iniziali_immobiliare": 30,
        "pagine_ordinarie_immobiliare": 1,
        "notifica_prima_esecuzione": False,
        "connessioni_parallele": 6,
    },
    "filtri": {
        "prezzo_min": 0,
        "prezzo_max": 0,
        "mq_min": 0,
        "mq_max": 0,
        "locali_min": 0,
        "escludi_stanze": False,
        "dati_mancanti": "invia",
        "prezzo_affitto_plausibile_max": 6000,
        "tipi_esclusi": ["negozio", "ufficio", "capannone", "magazzino", "laboratorio", "terreno",
                         "garage", "box", "posto auto", "locale commerciale"],
        "comuni_area": [],
        "frazioni_area": {},
        "zone_fuori_area": [],
        "fuori_zona": "scarta",
    },
    "notifiche": {
        "silenzio_dalle": "",
        "silenzio_alle": "",
        "max_messaggi_singoli": 15,
        "anteprima_link": True,
        "avvisi_guasti": True,
        "guasto_dopo_ore": 24,
        "avvisi_guasti_ogni_giorni": 7,
        "guasti_da_soli_dopo_giorni": 3,
    },
    "doppioni": {
        "finestra_giorni": 120,
        "tolleranza_prezzo_pct": 3,
        "tolleranza_mq": 3,
        "chiedi_llm_nei_dubbi": True,
    },
    "dettagli": {
        "leggi_pagina_annuncio": True,
        "anche_portali_se_mancano_dati": True,
        "max_pagine_per_controllo": 40,
    },
    "llm": {
        "usa": True,
        "file_modello": "llm.toml",
        "max_chiamate_per_controllo": 40,
        "max_chiamate_al_giorno": 300,
        "max_spesa_giornaliera_usd": 0.5,
    },
    "aggiornamento": {
        "automatico": True,
        "remoto": "origin",
        "ramo": "main",
    },
    "idealista": {
        "centro": "44.6471,10.9252",
        "raggio_metri": 16000,
        "since_date": "T",
        "ogni_ore": 12,
    },
}

SEGRETI = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "OPENROUTER_API_KEY", "IDEALISTA_API_KEY", "IDEALISTA_API_SECRET")
ALTRE_VARIABILI = ("OPENROUTER_MODEL", "LLM_CONFIG", "TELEGRAM_API_URL", "OPENROUTER_BASE_URL")


class Percorsi:
    """Dove stanno i file. I dati che cambiano (archivio, diario) vanno in stato/, ignorata da git."""

    def __init__(self, base: Path):
        self.base = Path(base).resolve()
        self.stato = self.base / "stato"
        self.db = self.stato / "radar.db"
        self.log = self.stato / "radar.log"
        self.lock = self.stato / "radar.lock"
        self.esportazione = self.stato / "annunci_visti.csv"
        self.env = self.base / ".env"
        self.fonti = self.base / "fonti" if (self.base / "fonti").is_dir() else self.base

    def prepara(self) -> None:
        self.stato.mkdir(parents=True, exist_ok=True)
        vecchio = self.base / "radar.db"  # prima versione: archivio accanto a radar.py
        if vecchio.exists() and not self.db.exists():
            shutil.move(str(vecchio), str(self.db))


def carica_config(base: Path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CFG)
    letti = []
    for nome in ("config.toml", "config.local.toml"):
        p = Path(base) / nome
        if not p.exists():
            continue
        with open(p, "rb") as f:
            dati = tomllib.load(f)
        for sez, valori in dati.items():
            if isinstance(valori, dict):
                cfg.setdefault(sez, {}).update(valori)
        # compatibilita' con la prima versione del radar
        es = dati.get("esecuzione", {})
        if "max_messaggi_singoli" in es and "max_messaggi_singoli" not in dati.get("notifiche", {}):
            cfg["notifiche"]["max_messaggi_singoli"] = es["max_messaggi_singoli"]
        fil = dati.get("filtri", {})
        if fil.get("zone_fuori_area_azione") and "fuori_zona" not in fil:
            cfg["filtri"]["fuori_zona"] = fil["zone_fuori_area_azione"]
        letti.append(nome)
    cfg["_letti"] = letti
    return cfg


def leggi_env(path: Path) -> Dict[str, str]:
    valori: Dict[str, str] = {}
    if not path.exists():
        return valori
    for riga in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        if riga.lower().startswith("export "):
            riga = riga[7:].strip()
        if "=" not in riga:
            continue
        chiave, valore = riga.split("=", 1)
        chiave, valore = chiave.strip(), valore.strip()
        if valore[:1] in ("'", '"') and valore.find(valore[0], 1) > 0:   # "valore"  # commento
            valore = valore[1:valore.find(valore[0], 1)]
        else:
            valore = re.split(r"\s+#", valore, maxsplit=1)[0].strip()
        valori[chiave] = valore
    return valori


def carica_segreti(percorsi: Percorsi, cfg: dict) -> Dict[str, str]:
    """Valori del file .env; le variabili d'ambiente del sistema hanno la precedenza."""
    env = leggi_env(percorsi.env)
    out = dict(env)
    for k in SEGRETI + ALTRE_VARIABILI:
        v = os.environ.get(k)
        if v:
            out[k] = v
        else:
            out.setdefault(k, "")
    # compatibilita': token e chiavi scritti in config.toml dalla prima versione
    tg = cfg.get("telegram", {}) or {}
    out["TELEGRAM_TOKEN"] = out["TELEGRAM_TOKEN"] or str(tg.get("token", "") or "").strip()
    out["TELEGRAM_CHAT_ID"] = out["TELEGRAM_CHAT_ID"] or str(tg.get("chat_id", "") or "").strip()
    ide = cfg.get("idealista", {}) or {}
    out["IDEALISTA_API_KEY"] = out["IDEALISTA_API_KEY"] or str(ide.get("api_key", "") or "").strip()
    out["IDEALISTA_API_SECRET"] = out["IDEALISTA_API_SECRET"] or str(ide.get("api_secret", "") or "").strip()
    return out


def scrivi_env(percorsi: Percorsi, valori: Dict[str, str]) -> Path:
    """Aggiorna (o crea) il file .env lasciando com'e' tutto il resto."""
    p = percorsi.env
    if p.exists():
        righe = p.read_text(encoding="utf-8-sig").splitlines()
    elif (percorsi.base / ".env.example").exists():
        righe = (percorsi.base / ".env.example").read_text(encoding="utf-8-sig").splitlines()
    else:
        righe = []
    fatti = set()
    for i, r in enumerate(righe):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", r)
        if m and m.group(1) in valori and m.group(1) not in fatti:
            righe[i] = f"{m.group(1)}={valori[m.group(1)]}"
            fatti.add(m.group(1))
    for k, v in valori.items():
        if k not in fatti:
            righe.append(f"{k}={v}")
    p.write_text("\n".join(righe) + "\n", encoding="utf-8")
    return p
