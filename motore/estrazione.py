"""Riconoscimento dei link agli annunci in una pagina di elenco (portali e siti delle agenzie)."""

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

PORTALI = {
    "immobiliare.it": re.compile(r"^https?://(?:www\.)?immobiliare\.it/(?:[a-z]{2}/)?annunci/(\d+)"),
    "casa.it": re.compile(r"^https?://(?:www\.)?casa\.it/immobili/(\d+)"),
    "subito.it": re.compile(r"^https?://(?:www\.)?subito\.it/[\w-]+/[\w-]*?-?(\d{6,})\.htm"),
    "wikicasa.it": re.compile(r"^https?://(?:www\.)?wikicasa\.it/(?:[a-z]{2}/)?annuncio/(\d+)"),
}
NOMI_PORTALI = {"immobiliare.it": "Immobiliare.it", "casa.it": "Casa.it", "subito.it": "Subito.it",
                "wikicasa.it": "Wikicasa", "idealista.it": "Idealista"}
SUGGERIMENTI = ("immobil", "annunc", "scheda", "propert", "propriet", "dettagli", "affitt", "locazion",
                "appartament", "villa", "rif", "detail", "listing", "offert", "abitazion", "casa-",
                "monolocale", "bilocale", "trilocale", "quadrilocale", "attico", "loft")
DA_SALTARE = ("privacy", "cookie", "contatt", "chi-siamo", "chisiamo", "login", "account", "wp-content",
              "wp-json", "/feed", "/tag/", "/category/", "/categoria/", "lavora-con-noi", "valutazion",
              "mutuo", "/blog", "/news", "/page/", "/pag/", "orderby=", "sort=", "ordina", "/cerca",
              "ricerca?", "facebook.com", "instagram.com", "linkedin", "youtube", "whatsapp",
              "newsletter", "registrati", "preferiti", "stampa", "print", "share", "condividi")
ESTENSIONI_FILE = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".mp4", ".css", ".js")
PARAMETRI_INUTILI = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$|from$)")

Trovati = dict[str, tuple[str, str, str]]   # uid -> (url, testo della scheda, titolo)


def leggi_html(html: str):
    """BeautifulSoup con il parser migliore disponibile (lxml, altrimenti quello standard).

    bs4 e lxml si importano qui e non in cima al file: aggiorna.py importa archivio, che importa questo modulo, e il
    processo che aggiorna le librerie con uv non deve averle gia' caricate (su Windows uv non puo' sostituire i file
    di una libreria in uso).
    """
    from bs4 import BeautifulSoup
    try:
        import lxml  # noqa: F401
        parser = "lxml"
    except ImportError:
        parser = "html.parser"
    return BeautifulSoup(html, parser)


def portale_di(url: str) -> str | None:
    """Portale riconosciuto dall'indirizzo. I mini-siti delle agenzie ospitati dai portali
    (es. nome.agenzie.casa.it) si leggono come siti normali; le vetrine Subito no."""
    host = urlparse(url).netloc.lower()
    for dom in PORTALI:
        if host in (dom, "www." + dom) or (dom == "subito.it" and host == "impresapiu.subito.it"):
            return dom
    return None


def pulisci_url(url: str) -> str:
    """Indirizzo da aprire e da mostrare: senza parametri di tracciamento e senza #ancora."""
    p = urlparse(url)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not PARAMETRI_INUTILI.match(k.lower())])
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.params, query, ""))


def canonico(url: str) -> str:
    """Identificativo stabile dell'annuncio: come pulisci_url, ma senza la barra finale."""
    p = urlparse(pulisci_url(url))
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", p.query, ""))


def dominio(url: str) -> str:
    h = urlparse(url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def stesso_sito(a: str, b: str) -> bool:
    ha, hb = dominio(a), dominio(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def sembra_annuncio(url: str, pagina: str) -> bool:
    if not stesso_sito(url, pagina):
        return False
    p = urlparse(url)
    path = p.path.lower()
    tutto = (path + "?" + p.query).lower()
    if path.endswith(ESTENSIONI_FILE) or any(s in tutto for s in DA_SALTARE):
        return False
    if canonico(url) == canonico(pagina):
        return False
    segmenti = [s for s in path.split("/") if s]
    ultimo = segmenti[-1] if segmenti else ""
    ha_id = bool(re.search(r"\d{3,}", tutto))
    ha_parola = any(h in tutto for h in SUGGERIMENTI)
    slug = ultimo.count("-") >= 2 and len(ultimo) >= 12
    return (ha_parola and (ha_id or slug)) or (ha_id and slug)


def testo_scheda(a, uid: str, uid_di: dict[int, str]) -> str:
    """Testo della 'card' che contiene il link: sale finche' il blocco contiene solo questo annuncio."""
    migliore = a.get_text(" ", strip=True)
    nodo = a
    for _ in range(6):
        nodo = nodo.parent
        if nodo is None or nodo.name in ("body", "html", "main"):
            break
        altri = {uid_di.get(id(x)) for x in nodo.find_all("a", href=True)} - {None, uid}
        if altri:  # il blocco contiene anche altri annunci: fermati
            break
        t = nodo.get_text(" ", strip=True)
        if len(t) > 800:
            break
        migliore = t
    return re.sub(r"\s+", " ", migliore)[:800]


def titolo_link(a) -> str:
    t = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
    if not t:
        img = a.find("img")
        if img is not None:
            t = (img.get("alt") or "").strip()
    return re.sub(r"\s+", " ", t)[:160]


def estrai(html: str, url_pagina: str, pattern: str = "") -> Trovati:
    """Restituisce {uid: (url, testo_scheda, titolo)} per ogni annuncio linkato nella pagina."""
    soup = leggi_html(html)
    portale = portale_di(url_pagina)
    regex = re.compile(pattern, re.I) if pattern else None
    candidati = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        assoluto = urljoin(url_pagina, href)
        if portale:
            m = PORTALI[portale].match(assoluto)
            if not m:
                continue
            uid = f"{portale}:{m.group(1)}"
            url = pulisci_url(assoluto)
        else:
            if regex is not None:
                if not regex.search(assoluto):
                    continue
            elif not sembra_annuncio(assoluto, url_pagina):
                continue
            url = pulisci_url(assoluto)
            uid = canonico(assoluto)
        candidati.append((a, uid, url))
    uid_di = {id(a): uid for a, uid, _ in candidati}
    trovati: Trovati = {}
    for a, uid, url in candidati:
        testo = testo_scheda(a, uid, uid_di)
        titolo = titolo_link(a)
        prima = trovati.get(uid)
        if prima is None:
            trovati[uid] = (url, testo, titolo)
        else:  # stesso annuncio linkato piu' volte (foto + titolo): tieni il testo piu' ricco
            trovati[uid] = (url, testo if len(testo) > len(prima[1]) else prima[1],
                            titolo if len(titolo) > len(prima[2]) else prima[2])
    return trovati


def pagina_immobiliare(url: str, n: int) -> str:
    if n <= 1:
        return url
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "pag"] + [("pag", str(n))]
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(q), ""))
