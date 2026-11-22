from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from concall.config import settings

log = logging.getLogger("concall.http")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

NSE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.in/companies-listing/corporate-filings-announcements",
}

BSE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/corporates/ann.html",
    "X-Requested-With": "XMLHttpRequest",
}

PDF_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


class RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = defaultdict(float)

    async def wait(self, host: str) -> None:
        lock = self._locks[host]
        async with lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            delay = self._last[host] + self.interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = loop.time()


limiter = RateLimiter(settings.min_request_interval)


class ExchangeClient:
    def __init__(self) -> None:
        self.nse = httpx.AsyncClient(
            headers=NSE_HEADERS,
            timeout=settings.request_timeout,
            follow_redirects=True,
        )
        self.bse = httpx.AsyncClient(
            headers=BSE_HEADERS,
            timeout=settings.request_timeout,
            follow_redirects=True,
        )
        self._nse_warm = False
        self._bse_warm = False

    async def aclose(self) -> None:
        await self.nse.aclose()
        await self.bse.aclose()

    async def warm_nse(self) -> None:
        if self._nse_warm:
            return
        await limiter.wait("www.nseindia.in")
        try:
            r = await self.nse.get(
                "https://www.nseindia.in/companies-listing/corporate-filings-announcements"
            )
            log.info("NSE warmup status=%s", r.status_code)
            self._nse_warm = r.status_code < 500
        except httpx.HTTPError as exc:
            log.warning("NSE warmup failed: %s", exc)

    async def warm_bse(self) -> None:
        if self._bse_warm:
            return
        await limiter.wait("www.bseindia.com")
        try:
            r = await self.bse.get("https://www.bseindia.com/corporates/ann.html")
            log.info("BSE warmup status=%s", r.status_code)
            self._bse_warm = r.status_code < 500
        except httpx.HTTPError as exc:
            log.warning("BSE warmup failed: %s", exc)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, ValueError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def nse_json(self, url: str, params: dict | None = None) -> object:
        await self.warm_nse()
        await limiter.wait("www.nseindia.com")
        r = await self.nse.get(url, params=params)
        if r.status_code in {401, 403}:
            self._nse_warm = False
            await self.warm_nse()
            await limiter.wait("www.nseindia.com")
            r = await self.nse.get(url, params=params)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" not in ctype and not r.text.lstrip().startswith(("[", "{")):
            raise ValueError(f"NSE non-JSON response ({r.status_code})")
        return r.json()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def bse_json(self, url: str, params: dict | None = None) -> object:
        await self.warm_bse()
        await limiter.wait("api.bseindia.com")
        r = await self.bse.get(url, params=params)
        if r.status_code in {401, 403}:
            self._bse_warm = False
            await self.warm_bse()
            await limiter.wait("api.bseindia.com")
            r = await self.bse.get(url, params=params)
        r.raise_for_status()
        return r.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def download_bytes(self, url: str, referer: str) -> bytes:
        host = httpx.URL(url).host or "download"
        await limiter.wait(host)
        headers = {**PDF_HEADERS, "Referer": referer}
        # Prefer the client whose cookies/origin match the host.
        client = self.nse if "nseindia" in url else self.bse
        r = await client.get(url, headers=headers)
        if r.status_code in {401, 403} and "nseindia" in url:
            self._nse_warm = False
            await self.warm_nse()
            await limiter.wait(host)
            r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.content
