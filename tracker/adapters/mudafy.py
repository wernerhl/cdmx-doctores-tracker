# Portal ToS may prohibit scraping. Output is for private research only.
# mudafy.com.mx uses React Server Components. Property data is embedded
# in self.__next_f.push() chunks in the SSR HTML.

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

RSC_FIELDS = [
    ("pub_id", r'"id":(\d+)'),
    ("price", r'"amount":(\d+)'),
    ("covered_area", r'"covered_area":([\d.]+)'),
    ("total_area", r'"total_area":([\d.]+)'),
    ("bedrooms", r'"bedrooms":(\d+)'),
    ("bathrooms", r'"bathrooms":(\d+)'),
    ("parking", r'"parking_lots":(\d+)'),
    ("neighborhood", r'"neighborhood":"([^"\\]+)"'),
    ("city", r'"city":"([^"\\]+)"'),
    ("state", r'"state":"([^"\\]+)"'),
    ("lat", r'"latitude":([\d.-]+)'),
    ("lon", r'"longitude":([\d.-]+)'),
    ("full_address", r'"full_address":"([^"\\]+)"'),
    ("slug", r'"slug":"([^"\\]+)"'),
]


class MudafyAdapter(SourceAdapter):
    name = "mudafy"

    def build_search_urls(self) -> list[str]:
        urls = []
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        for page_num in range(1, 8):
            if page_num == 1:
                base = "https://mudafy.com.mx/venta/propiedades/cdmx_o_cuauhtemoc"
            else:
                base = f"https://mudafy.com.mx/venta/propiedades/cdmx_o_cuauhtemoc/{page_num}-p"
            url = f"{base}?tipo=departamento&precio_min={price_min}&precio_max={price_max}&recamaras_min=2"
            urls.append(url)
        return urls

    async def fetch(self, browser_context, url: str) -> Optional[RawPage]:
        """Use direct HTTP instead of Playwright — mudafy SSR HTML has all data."""
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status >= 400:
                    logger.warning(f"[{self.name}] HTTP {resp.status} for {url}")
                    return None
                html = resp.read().decode("utf-8", errors="replace")

            self._cache_page(url, html)

            # Parse RSC chunks for publication data
            listings_data = self._extract_rsc_publications(html)
            raw_page = RawPage(url=url, html=html, status_code=200)
            raw_page.json_data = {"publications": listings_data}
            return raw_page
        except Exception as e:
            logger.warning(f"[{self.name}] fetch error for {url}: {e}")
            return None

    def _extract_rsc_publications(self, html: str) -> list[dict]:
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',
            html, re.DOTALL
        )
        results = []
        seen_ids = set()

        for chunk in chunks:
            if "publication" not in chunk or "amount" not in chunk:
                continue

            unescaped = chunk.replace('\\"', '"').replace("\\n", "\n")
            fields = {}
            for field_name, pattern in RSC_FIELDS:
                m = re.search(pattern, unescaped)
                if m:
                    fields[field_name] = m.group(1)

            pub_id = fields.get("pub_id")
            if pub_id and pub_id not in seen_ids and fields.get("price"):
                seen_ids.add(pub_id)
                results.append(fields)

        return results

    def parse_list(self, page: RawPage) -> list[RawListing]:
        listings = []
        pubs = (page.json_data or {}).get("publications", [])

        for pub in pubs:
            listing = RawListing(source=self.name, listing_type="sale")
            listing.source_listing_id = pub.get("pub_id", "")
            slug = pub.get("slug", "")
            listing.url = f"https://mudafy.com.mx/propiedades/{slug}" if slug else ""
            listing.price_mxn = _safe_int(pub.get("price"))
            listing.area_m2 = _safe_float(pub.get("total_area")) or _safe_float(pub.get("covered_area"))
            listing.bedrooms = _safe_int(pub.get("bedrooms"))
            listing.bathrooms = _safe_float(pub.get("bathrooms"))
            listing.parking = _safe_int(pub.get("parking"))
            listing.lat = _safe_float(pub.get("lat"))
            listing.lon = _safe_float(pub.get("lon"))
            listing.address_raw = pub.get("full_address", "")
            listing.colonia = pub.get("neighborhood", "")
            listing.alcaldia = pub.get("city", "Cuauhtémoc")

            # Infer colonia from address if not set
            if not listing.colonia and listing.address_raw:
                listing.colonia = _infer_colonia(listing.address_raw)

            if listing.source_listing_id:
                listings.append(listing)

        return listings

    async def fetch_rental_listings(self, browser_context) -> list[RawListing]:
        urls = []
        for page_num in range(1, 5):
            if page_num == 1:
                base = "https://mudafy.com.mx/renta/propiedades/cdmx_o_cuauhtemoc"
            else:
                base = f"https://mudafy.com.mx/renta/propiedades/cdmx_o_cuauhtemoc/{page_num}-p"
            url = f"{base}?tipo=departamento&recamaras_min=2"
            urls.append(url)

        all_listings = []
        for url in urls:
            self._delay()
            page = await self.fetch(browser_context, url)
            if page is None:
                continue
            rentals = self.parse_list(page)
            for r in rentals:
                r.listing_type = "rent"
            all_listings.extend(rentals)
        return all_listings


def _infer_colonia(address: str) -> str:
    addr_lower = address.lower()
    colonia_map = {
        "doctores": "Doctores",
        "obrera": "Obrera",
        "algarín": "Algarín",
        "algarin": "Algarín",
        "buenos aires": "Buenos Aires",
        "centro hist": "Centro",
        "guerrero": "Guerrero",
        "roma sur": "Roma Sur",
        "roma norte": "Roma Norte",
        "santa maría la ribera": "Santa María la Ribera",
        "sta maria la ribera": "Santa María la Ribera",
        "del valle": "Del Valle",
        "narvarte": "Narvarte",
        "condesa": "Condesa",
        "juárez": "Juárez",
        "tabacalera": "Tabacalera",
        "san rafael": "San Rafael",
        "verónica anzúres": "Verónica Anzúres",
        "veronica anzures": "Verónica Anzúres",
        "nápoles": "Nápoles",
        "polanco": "Polanco",
    }
    for key, colonia in colonia_map.items():
        if key in addr_lower:
            return colonia
    return ""


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
