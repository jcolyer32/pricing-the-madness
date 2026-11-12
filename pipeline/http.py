"""
Polite, robust web fetching.

Every page request in the project goes through `Fetcher.get()`, so these rules
apply everywhere:
  * browser-like headers (some sites refuse obvious scripts)
  * retries with increasing waits on errors / rate limits
  * a pause between requests to the same site
  * a short on-disk cache, so re-running doesn't re-download everything
  * a manual fallback: if a site blocks the script, save the page from your
    browser into data/manual_html/<key>.html and it will be used instead
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# curl_cffi imitates a real Chrome browser at the network level, which gets past
# most "are you a bot?" blocks. It's optional: if it isn't installed we fall back
# to the standard `requests` library.
try:
    from curl_cffi import requests as cffi_requests  # type: ignore
    HAVE_CFFI = True
except ImportError:  # pragma: no cover
    HAVE_CFFI = False
import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, root: Path, delay: float = 3, timeout: float = 30,
                 retries: int = 3, cache_minutes: float = 60, use_cache: bool = True,
                 host_delays: dict | None = None):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.host_delays = host_delays or {}
        self.cache_seconds = cache_minutes * 60 if use_cache else 0
        self.cache_dir = root / "data" / "cache"
        self.manual_dir = root / "data" / "manual_html"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manual_dir.mkdir(parents=True, exist_ok=True)
        self._last_hit: dict[str, float] = {}
        if HAVE_CFFI:
            self.session = cffi_requests.Session(impersonate="chrome")
        else:
            self.session = requests.Session()
            self.session.headers.update(HEADERS)

    # ------------------------------------------------------------------
    def get(self, url: str, key: str | None = None) -> str:
        """Return the text of `url`. `key` names the cache/manual file."""
        key = key or hashlib.md5(url.encode()).hexdigest()[:12]
        cache_file = self.cache_dir / f"{key}.cache"

        # 1) fresh cache?
        if self.cache_seconds and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < self.cache_seconds:
                log.debug("cache hit %s", key)
                return cache_file.read_text(encoding="utf-8")

        # 2) live fetch with retries
        try:
            text = self._fetch_with_retries(url)
            cache_file.write_text(text, encoding="utf-8")
            return text
        except FetchError as err:
            # 3) stale cache beats nothing (e.g. offline): use the last downloaded copy
            if cache_file.exists():
                log.warning("Live fetch failed for %s (%s) — using cached copy from %s", url, err,
                            time.strftime("%Y-%m-%d %H:%M", time.localtime(cache_file.stat().st_mtime)))
                return cache_file.read_text(encoding="utf-8")
            # 4) manual fallback
            manual = self.manual_dir / f"{key}.html"
            if manual.exists():
                log.warning("Live fetch failed for %s (%s) — using manual file %s",
                            url, err, manual.name)
                return manual.read_text(encoding="utf-8", errors="replace")
            hint = ""
            if "403" in str(err) and not HAVE_CFFI:
                hint = " Tip: run `pip install curl_cffi` (see README) to get past bot blocks."
            raise FetchError(f"{err}.{hint} Or save the page from your browser as "
                             f"data/manual_html/{key}.html") from err

    # ------------------------------------------------------------------
    def _polite_wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.host_delays.get(host, self.delay)
        wait = self._last_hit.get(host, 0) + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.time()

    def _fetch_with_retries(self, url: str) -> str:
        last_err = "unknown error"
        for attempt in range(1, self.retries + 1):
            self._polite_wait(url)
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 200 and r.text.strip():
                    return r.text
                last_err = f"HTTP {r.status_code}"
                if r.status_code in (403, 404):      # retrying won't help
                    break
            except Exception as exc:                  # network hiccup, timeout…
                last_err = f"{type(exc).__name__}: {exc}"
            backoff = self.delay * (2 ** attempt)
            log.info("  retry %d/%d for %s in %.0fs (%s)",
                     attempt, self.retries, url, backoff, last_err)
            time.sleep(backoff)
        raise FetchError(f"Could not fetch {url} ({last_err})")
