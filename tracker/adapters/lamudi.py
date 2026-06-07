# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

COLONIA_SLUGS = {
    "Doctores": "doctores",
    "Obrera": "obrera",
    "Algarín": "algarin",
    "Buenos Aires": "buenos-aires",
    "Centro": "centro-historico",
    "Guerrero": "guerrero",
    "Roma Sur": "roma-sur",
    "Santa María la Ribera": "santa-maria-la-ribera",
}


class LamudiAdapter(SourceAdapter):
    name = "lamudi"

    def build_search_urls(self) -> list[str]:
        urls = []
        colonias = self.config.get("colonias", [])
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        for col in colonias:
            slug = COLONIA_SLUGS.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 6):
                url = (
                    f"https://www.lamudi.com.mx/distrito-federal/cuauhtemoc/"
                    f"{slug}/departamento/for-sale/"
                    f"?price_from={price_min}&price_to={price_max}"
                    f"&bedrooms=2&size=30&page={page_num}"
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
                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    if data.get("@type") == "ItemList":
                        items = [e.get("item", e) for e in data.get("itemListElement", [])]
                    elif data.get("@type") in ("Product", "Apartment", "RealEstateListing"):
                        items = [data]
                for item in items:
                    listing = self._from_json_ld(item)
                    if listing:
                        listings.append(listing)
            except json.JSONDecodeError:
                continue

        if not listings:
            listings = self._parse_dom(page.html)

        return listings

    def _from_json_ld(self, data: dict) -> Optional[RawListing]:
        if data.get("@type") not in ("Product", "Apartment", "Residence", "RealEstateListing", "Place"):
            return None
        listing = RawListing(source=self.name, listing_type="sale")
        listing.url = data.get("url", "")
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

        self._extract_features(listing)
        return listing

    def _parse_dom(self, html: str) -> list[RawListing]:
        listings = []
        card_pat = re.compile(
            r'<div[^>]*class="[^"]*listing-card[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            re.DOTALL | re.IGNORECASE
        )
        for m in card_pat.finditer(html):
            card = m.group(1)
            listing = RawListing(source=self.name, listing_type="sale")
            listing.raw_html = card

            link = re.search(r'href="(https?://www\.lamudi\.com\.mx/[^"]+)"', card)
            if link:
                listing.url = link.group(1)
                id_match = re.search(r"/(\d{5,})", listing.url)
                if id_match:
                    listing.source_listing_id = id_match.group(1)

            title_m = re.search(r'<(?:h2|h3|a)[^>]*>(.*?)</(?:h2|h3|a)>', card, re.DOTALL)
            if title_m:
                listing.title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()

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
            slug = COLONIA_SLUGS.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 4):
                url = (
                    f"https://www.lamudi.com.mx/distrito-federal/cuauhtemoc/"
                    f"{slug}/departamento/for-rent/"
                    f"?bedrooms=2&size=30&page={page_num}"
                )
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


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
