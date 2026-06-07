# Portal ToS may prohibit scraping. Output is for private research only.
# propiedades.com disallows crawling in robots.txt.
# Only attempt when PROXY_URL is set; otherwise skip and log.

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

COLONIA_SLUGS = {
    "Doctores": "doctores",
    "Obrera": "obrera",
    "Algarín": "algarin",
    "Buenos Aires": "buenos-aires",
    "Centro": "centro",
    "Guerrero": "guerrero",
    "Roma Sur": "roma-sur",
    "Santa María la Ribera": "santa-maria-la-ribera",
}


class PropiedadesAdapter(SourceAdapter):
    name = "propiedades"

    def __init__(self, config, run_date):
        super().__init__(config, run_date)
        if not os.environ.get("PROXY_URL"):
            self.reachable = False
            logger.info(f"[{self.name}] skipped:robots — PROXY_URL not set")

    def build_search_urls(self) -> list[str]:
        if not self.reachable:
            return []
        urls = []
        colonias = self.config.get("colonias", [])
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        for col in colonias:
            slug = COLONIA_SLUGS.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 4):
                url = (
                    f"https://propiedades.com/departamentos-en-venta/"
                    f"cuauhtemoc/{slug}"
                    f"?precio_min={price_min}&precio_max={price_max}"
                    f"&recamaras=2&pagina={page_num}"
                )
                urls.append(url)
        return urls

    def parse_list(self, page: RawPage) -> list[RawListing]:
        listings = []
        json_ld_matches = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            page.html, re.DOTALL
        )
        for match in json_ld_matches:
            try:
                data = json.loads(match)
                if isinstance(data, dict) and data.get("@type") in ("Product", "RealEstateListing", "Apartment"):
                    listing = self._from_json_ld(data)
                    if listing:
                        listings.append(listing)
            except json.JSONDecodeError:
                continue

        if not listings:
            listings = self._parse_dom(page.html)
        return listings

    def _from_json_ld(self, data: dict) -> Optional[RawListing]:
        listing = RawListing(source=self.name, listing_type="sale")
        listing.url = data.get("url", "")
        listing.title = data.get("name", "")
        listing.description_raw = data.get("description", "")
        listing.source_listing_id = data.get("sku", "")
        if not listing.source_listing_id:
            id_match = re.search(r"/(\d{5,})", listing.url)
            if id_match:
                listing.source_listing_id = id_match.group(1)

        offers = data.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_str = re.sub(r"[^\d.]", "", str(offers.get("price", "")))
        if price_str:
            listing.price_mxn = int(float(price_str))

        self._extract_features(listing)
        return listing if listing.source_listing_id else None

    def _parse_dom(self, html: str) -> list[RawListing]:
        listings = []
        card_pat = re.compile(
            r'<div[^>]*class="[^"]*(?:property|listing|card)[^"]*"[^>]*>(.*?)</div>\s*</div>',
            re.DOTALL | re.IGNORECASE
        )
        for m in card_pat.finditer(html):
            card = m.group(1)
            listing = RawListing(source=self.name, listing_type="sale")
            listing.raw_html = card

            link = re.search(r'href="([^"]*)"', card)
            if link:
                listing.url = link.group(1)
                if not listing.url.startswith("http"):
                    listing.url = "https://propiedades.com" + listing.url
                id_match = re.search(r"/(\d{5,})", listing.url)
                if id_match:
                    listing.source_listing_id = id_match.group(1)

            price_m = re.search(r'\$[\s]*([\d,]+)', card)
            if price_m:
                listing.price_mxn = int(re.sub(r"[^\d]", "", price_m.group(1)))

            self._extract_features(listing)
            if listing.source_listing_id:
                listings.append(listing)
        return listings

    def _extract_features(self, listing: RawListing):
        text = f"{listing.title} {listing.description_raw} {listing.raw_html}"
        area = re.search(r'(\d+(?:\.\d+)?)\s*m[²2]', text)
        if area:
            listing.area_m2 = float(area.group(1))
        beds = re.search(r'(\d+)\s*(?:rec[áa]mara|dorm)', text, re.IGNORECASE)
        if beds:
            listing.bedrooms = int(beds.group(1))
        baths = re.search(r'(\d+(?:\.\d+)?)\s*ba[ñn]o', text, re.IGNORECASE)
        if baths:
            listing.bathrooms = float(baths.group(1))
