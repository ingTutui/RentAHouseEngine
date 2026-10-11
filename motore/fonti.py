"""Le fonti da controllare: fonti/fonti_portali.csv e fonti/fonti_agenzie.csv."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Fonte:
    tipo: str            # "portale" | "agenzia"
    nome: str
    comune: str
    url: str
    js: bool = False
    pattern: str = ""


def si(v: str) -> bool:
    return (v or "").strip().lower() in ("si", "sì", "s", "yes", "y", "1", "true", "x", "probabile")


def _spenta(v: str) -> bool:
    return (v or "sì").strip().lower() in ("no", "0", "n", "false")


def leggi_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    for codifica in ("utf-8-sig", "cp1252"):  # cp1252: file risalvato da Excel su Windows
        try:
            with open(path, encoding=codifica, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                sep = ";" if sample.count(";") >= sample.count(",") else ","
                righe = list(csv.DictReader(f, delimiter=sep))
            return [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in righe]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Non riesco a leggere {path.name}: salvalo come CSV UTF-8")


def carica_fonti(cartella: Path) -> list[Fonte]:
    fonti: list[Fonte] = []
    for r in leggi_csv(cartella / "fonti_portali.csv"):
        if _spenta(r.get("attiva", "")) or not (r.get("url") or "").strip():
            continue
        fonti.append(Fonte("portale", (r.get("portale") or "").strip(), (r.get("comune") or "").strip(),
                           r["url"].strip()))
    for r in leggi_csv(cartella / "fonti_agenzie.csv"):
        if _spenta(r.get("attiva", "")):
            continue
        url = (r.get("url_affitti") or "").strip()
        if url.startswith("http"):
            fonti.append(Fonte("agenzia", (r.get("agenzia") or "").strip(), (r.get("comune") or "").strip(),
                               url, js=si(r.get("js", "")), pattern=(r.get("pattern_link") or "").strip()))
    return fonti
