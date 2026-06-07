# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

COLONIA_CODES = {
    "Doctores": "doctores",
    "Obrera": "obrera",
    "Algarín": "algarin",
    "Buenos Aires": "buenos-aires",
    "Centro": "centro",
    "Guerrero": "guerrero",
    "Roma Sur": "roma-sur",
    "Santa María la Ribera": "santa-maria-la-ribera",
}


class IcasasAdapter(SourceAdapter):
    name = "icasas"

    def build_search_urls(self) -> list[str]:
        urls = []
        colonias = self.config.get("colonias", [])
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        for col in colonias:
            slug = COLONIA_CODES.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 6):
                url = (
                    f"https://www.icasas.mx/venta/departamentos/"
                    f"distrito-federal/cuauhtemoc/{slug}/"
                    f"?precio_desde={price_min}&precio_hasta={price_max}"
                    f"&recamaras=2&pagina={page_num}"
                )
                urls.append(url)
        return urls

    def parse_list(self, page: RawPage) -> list[RawListing]:
        listings = []
        html = page.html

        json_ld_matches = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        for match in json_ld_matches:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    for item in data:
                        listing = self._parse_json_ld(item, page.url)
                        if listing:
                            listings.append(listing)
                elif isinstance(data, dict):
                    if data.get("@type") == "ItemList":
                        for elem in data.get("itemListElement", []):
                            item = elem.get("item", elem)
                            listing = self._parse_json_ld(item, page.url)
                            if listing:
                                listings.append(listing)
                    else:
                        listing = self._parse_json_ld(data, page.url)
                        if listing:
                            listings.append(listing)
            except json.JSONDecodeError:
                continue

        if not listings:
            listings = self._parse_dom(html, page.url)

        return listings

    def _parse_json_ld(self, data: dict, page_url: str) -> Optional[RawListing]:
        if data.get("@type") not in ("Product", "Apartment", "Residence", "RealEstateListing"):
            return None
        listing = RawListing(source=self.name, listing_type="sale")
        listing.url = data.get("url", page_url)
        listing.title = data.get("name", "")
        listing.description_raw = data.get("description", "")

        offers = data.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_str = str(offers.get("price", ""))
        price_str = re.sub(r"[^\d.]", "", price_str)
        if price_str:
            listing.price_mxn = int(float(price_str))

        listing.source_listing_id = data.get("sku", "") or data.get("productID", "")
        if not listing.source_listing_id:
            id_match = re.search(r"/(\d{5,})", listing.url)
            if id_match:
                listing.source_listing_id = id_match.group(1)

        geo = data.get("geo", {})
        if geo:
            listing.lat = _safe_float(geo.get("latitude"))
            listing.lon = _safe_float(geo.get("longitude"))

        addr = data.get("address", {})
        if isinstance(addr, dict):
            listing.colonia = addr.get("addressLocality", "")
            listing.alcaldia = addr.get("addressRegion", "")
            listing.address_raw = addr.get("streetAddress", "")

        self._extract_features_from_text(listing)
        return listing

    def _parse_dom(self, html: str, page_url: str) -> list[RawListing]:
        listings = []
        card_pattern = re.compile(
            r'<div[^>]*class="[^"]*(?:listing|property|card)[^"]*"[^>]*>(.*?)</div>\s*</div>',
            re.DOTALL | re.IGNORECASE
        )
        for match in card_pattern.finditer(html):
            card_html = match.group(1)
            listing = RawListing(source=self.name, listing_type="sale")
            listing.raw_html = card_html

            link = re.search(r'href="([^"]*)"', card_html)
            if link:
                url = link.group(1)
                if not url.startswith("http"):
                    url = "https://www.icasas.mx" + url
                listing.url = url
                id_match = re.search(r"/(\d{5,})", url)
                if id_match:
                    listing.source_listing_id = id_match.group(1)

            title_match = re.search(r'<(?:h2|h3|a)[^>]*>(.*?)</(?:h2|h3|a)>', card_html, re.DOTALL)
            if title_match:
                listing.title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

            price_match = re.search(r'\$[\s]*([\d,]+(?:\.\d+)?)', card_html)
            if price_match:
                listing.price_mxn = int(re.sub(r"[^\d]", "", price_match.group(1)))

            self._extract_features_from_text(listing)
            if listing.source_listing_id:
                listings.append(listing)

        return listings

    def _extract_features_from_text(self, listing: RawListing):
        text = f"{listing.title} {listing.description_raw} {listing.raw_html}"
        area = re.search(r'(\d+(?:\.\d+)?)\s*m[²2]', text)
        if area:
            listing.area_m2 = float(area.group(1))
        beds = re.search(r'(\d+)\s*(?:rec[áa]mara|dormitorio|hab)', text, re.IGNORECASE)
        if beds:
            listing.bedrooms = int(beds.group(1))
        baths = re.search(r'(\d+(?:\.\d+)?)\s*ba[ñn]o', text, re.IGNORECASE)
        if baths:
            listing.bathrooms = float(baths.group(1))
        park = re.search(r'(\d+)\s*estacionamiento', text, re.IGNORECASE)
        if park:
            listing.parking = int(park.group(1))

    async def fetch_rental_listings(self, browser_context) -> list[RawListing]:
        urls = []
        colonias = self.config.get("colonias", [])
        for col in colonias:
            slug = COLONIA_CODES.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 4):
                url = (
                    f"https://www.icasas.mx/renta/departamentos/"
                    f"distrito-federal/cuauhtemoc/{slug}/"
                    f"?recamaras=2&pagina={page_num}"
                )
                urls.append(url)

        all_listings: list[RawListing] = []
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


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
