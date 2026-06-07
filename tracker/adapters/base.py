# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache/raw")


@dataclass
class RawListing:
    source: str = ""
    source_listing_id: str = ""
    url: str = ""
    title: str = ""
    description_raw: str = ""
    price_mxn: Optional[int] = None
    area_m2: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    parking: Optional[int] = None
    floor: Optional[str] = None
    has_elevator: Optional[bool] = None
    year_built: Optional[int] = None
    colonia: str = ""
    alcaldia: str = ""
    address_raw: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_new_development: bool = False
    listing_type: str = "sale"  # "sale" or "rent"
    raw_html: str = ""
    raw_json: Optional[dict] = None


@dataclass
class RawPage:
    url: str
    html: str = ""
    json_data: Optional[dict] = None
    status_code: int = 0


class SourceAdapter(ABC):
    name: str = ""
    reachable: bool = True

    def __init__(self, config: dict, run_date: date):
        self.config = config
        self.run_date = run_date
        self._rate_limit_min = config.get("rate_limit", {}).get("min_delay_s", 2.0)
        self._rate_limit_max = config.get("rate_limit", {}).get("max_delay_s", 5.0)

    def _delay(self):
        delay = random.uniform(self._rate_limit_min, self._rate_limit_max)
        time.sleep(delay)

    def _cache_dir(self) -> Path:
        d = CACHE_DIR / self.name / str(self.run_date)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_page(self, url: str, content: str):
        slug = hashlib.md5(url.encode()).hexdigest()[:12]
        path = self._cache_dir() / f"{slug}.html.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(content)

    @abstractmethod
    def build_search_urls(self) -> list[str]:
        ...

    @abstractmethod
    def parse_list(self, page: RawPage) -> list[RawListing]:
        ...

    def parse_detail(self, page: RawPage, url: str) -> Optional[RawListing]:
        return None

    def healthcheck(self) -> bool:
        return self.reachable

    async def fetch(self, browser_context, url: str) -> Optional[RawPage]:
        try:
            page = await browser_context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp is None or resp.status >= 400:
                logger.warning(f"[{self.name}] HTTP {resp.status if resp else 'None'} for {url}")
                self.reachable = False
                await page.close()
                return None
            html = await page.content()
            await page.close()
            self._cache_page(url, html)
            return RawPage(url=url, html=html, status_code=resp.status)
        except Exception as e:
            logger.warning(f"[{self.name}] fetch error for {url}: {e}")
            self.reachable = False
            return None

    async def fetch_page_obj(self, browser_context, url: str):
        """Return the live Playwright page (caller must close it)."""
        try:
            page = await browser_context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp is None or resp.status >= 400:
                logger.warning(f"[{self.name}] HTTP {resp.status if resp else 'None'} for {url}")
                await page.close()
                return None
            return page
        except Exception as e:
            logger.warning(f"[{self.name}] fetch error for {url}: {e}")
            return None

    async def fetch_all_listings(self, browser_context) -> list[RawListing]:
        urls = self.build_search_urls()
        all_listings: list[RawListing] = []
        consecutive_failures = 0
        for url in urls:
            self._delay()
            page = await self.fetch(browser_context, url)
            if page is None:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logger.warning(f"[{self.name}] 3 consecutive failures — stopping")
                    break
                continue
            consecutive_failures = 0
            listings = self.parse_list(page)
            all_listings.extend(listings)
            logger.info(f"[{self.name}] {url} → {len(listings)} listings")
        self.reachable = len(all_listings) > 0 or consecutive_failures < 3
        logger.info(f"[{self.name}] total raw listings: {len(all_listings)}")
        return all_listings

    async def fetch_rental_listings(self, browser_context) -> list[RawListing]:
        return []
