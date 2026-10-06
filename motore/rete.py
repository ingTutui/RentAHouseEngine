"""Scaricamento delle pagine: robots.txt, pause tra richieste allo stesso sito, browser per i siti in JavaScript."""

import logging
import random
import time
from urllib import robotparser
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
log = logging.getLogger("radar")


class Scaricatore:
    def __init__(self, cfg: dict):
        e = cfg["esecuzione"]
        self.timeout = e["timeout_sec"]
        self.pausa = e["pausa_stesso_sito_sec"]
        self.usa_browser = e["usa_browser_per_js"]
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
        })
        self.ultimo: dict[str, float] = {}
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._pw = None
        self._browser = None

    def _attendi(self, host: str) -> None:
        t = self.ultimo.get(host)
        if t is not None:
            mancano = self.pausa + random.uniform(0, 1.5) - (time.monotonic() - t)
            if mancano > 0:
                time.sleep(mancano)

    def consentito(self, url: str) -> tuple[bool, str]:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code >= 500:
                    self.robots[base] = None          # server in errore: riprova al prossimo giro
                    return False, f"robots.txt non disponibile (HTTP {r.status_code})"
                rp.parse(r.text.splitlines() if r.status_code < 400 else [])  # 4xx = nessuna regola
            except requests.RequestException as ex:
                return False, f"sito non raggiungibile ({type(ex).__name__})"
            self.robots[base] = rp
        rp = self.robots[base]
        if rp is None:
            del self.robots[base]
            return False, "robots.txt non disponibile"
        if not rp.can_fetch(UA, url):
            return False, "vietato da robots.txt"
        return True, ""

    def scarica(self, url: str, js: bool = False) -> tuple[str | None, str]:
        ok, motivo = self.consentito(url)
        if not ok:
            return None, motivo
        host = urlparse(url).netloc.lower()
        self._attendi(host)
        if js and self.usa_browser:
            html = self._scarica_browser(url)
            self.ultimo[host] = time.monotonic()
            if html:
                return html, ""
        errore = ""
        for tentativo in (1, 2):
            try:
                r = self.s.get(url, timeout=self.timeout, allow_redirects=True)
                self.ultimo[host] = time.monotonic()
                if r.status_code == 200:
                    if not r.encoding or r.encoding.lower() == "iso-8859-1":
                        r.encoding = r.apparent_encoding
                    return r.text, ""
                if r.status_code in (401, 403, 429):
                    return None, f"HTTP {r.status_code}: il sito rifiuta le richieste automatiche"
                errore = f"HTTP {r.status_code}"
            except requests.RequestException as ex:
                errore = f"errore di rete ({type(ex).__name__})"
            if tentativo == 1:
                time.sleep(3)
        return None, errore

    def _scarica_browser(self, url: str) -> str | None:
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            page = self._browser.new_page(user_agent=UA, locale="it-IT")
            try:
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 2000)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                page.close()
        except ImportError:
            log.warning("Playwright non installato: %s letto senza browser (vedi README)", url)
            self.usa_browser = False
        except Exception as ex:  # timeout, chiusura inattesa, ecc.
            log.warning("Browser: errore su %s (%s)", url, type(ex).__name__)
        return None

    def chiudi(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._browser = self._pw = None
