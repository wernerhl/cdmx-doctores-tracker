# Portal ToS may prohibit scraping. Output is for private research only.
# inmuebles24.com uses DataDome — expect frequent blocks on datacenter IPs.

from __future__ import annotations

import json
import logging
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


class Inmuebles24Adapter(SourceAdapter):
    name = "inmuebles24"

    def build_search_urls(self) -> list[str]:
        urls = []
        colonias = self.config.get("colonias", [])
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        for col in colonias:
            slug = COLONIA_SLUGS.get(col["name"], col["name"].lower().replace(" ", "-"))
            for page_num in range(1, 4):
                url = (
                    f"https://www.inmuebles24.com/departamentos-en-venta-en-"
                    f"{slug}-cuauhtemoc.html"
                    f"?precio-desde={price_min}&precio-hasta={price_max}"
                    f"&recamaras-desde=2&pagina={page_num}"
                )
                urls.append(url)
        return urls

    async def fetch(self, browser_context, url: str) -> Optional[RawPage]:
        try:
            page = await browser_context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp is None or resp.status >= 400:
                logger.warning(f"[{self.name}] blocked/error HTTP {resp.status if resp else 'None'} for {url}")
                self.reachable = False
                await page.close()
                return None

            html = await page.content()
            if "captcha" in html.lower() or "datadome" in html.lower():
                logger.warning(f"[{self.name}] DataDome captcha detected for {url}")
                self.reachable = False
                await page.close()
                return None

            await page.close()
            self._cache_page(url, html)
            return RawPage(url=url, html=html, status_code=resp.status)
        except Exception as e:
            logger.warning(f"[{self.name}] fetch error: {e}")
            self.reachable = False
            return None

    def parse_list(self, page: RawPage) -> list[RawListing]:
        listings = []

        json_api_match = re.search(
            r'window\.__NEXT_DATA__\s*=\s*(\{.*?\});?\s*</script>',
            page.html, re.DOTALL
        )
        if json_api_match:
            try:
                next_data = json.loads(json_api_match.group(1))
                props = next_data.get("props", {}).get("pageProps", {})
                postings = props.get("listPostings", props.get("results", []))
                if isinstance(postings, dict):
                    postings = postings.get("listPostings", [])
                for p in postings:
                    listing = self._from_api(p)
                    if listing:
                        listings.append(listing)
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug(f"[{self.name}] NEXT_DATA parse failed: {e}")

        if not listings:
            listings = self._parse_dom(page.html)

        return listings

    def _from_api(self, data: dict) -> Optional[RawListing]:
        listing = RawListing(source=self.name, listing_type="sale")
        listing.source_listing_id = str(data.get("postingId", data.get("id", "")))
        listing.url = data.get("url", "")
        if listing.url and not listing.url.startswith("http"):
            listing.url = "https://www.inmuebles24.com" + listing.url
        listing.title = data.get("title", "")
        listing.description_raw = data.get("description", "")
        listing.price_mxn = _safe_int(data.get("priceOperationTypes", [{}])[0].get("price")
                                       if data.get("priceOperationTypes") else data.get("price"))

        listing.area_m2 = _safe_float(data.get("totalArea", data.get("coveredArea")))
        listing.bedrooms = _safe_int(data.get("bedrooms"))
        listing.bathrooms = _safe_float(data.get("bathrooms"))
        listing.parking = _safe_int(data.get("parkingLots"))

        loc = data.get("location", {})
        listing.lat = _safe_float(loc.get("lat"))
        listing.lon = _safe_float(loc.get("lon"))
        listing.address_raw = loc.get("address", "")
        listing.colonia = loc.get("neighborhood", "")
        listing.alcaldia = loc.get("city", "")

        return listing if listing.source_listing_id else None

    def _parse_dom(self, html: str) -> list[RawListing]:
        listings = []
        card_pat = re.compile(
            r'<div[^>]*data-posting-id="(\d+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            re.DOTALL
        )
        for m in card_pat.finditer(html):
            pid, card = m.group(1), m.group(2)
            listing = RawListing(source=self.name, listing_type="sale")
            listing.source_listing_id = pid
            listing.raw_html = card

            link = re.search(r'href="([^"]*)"', card)
            if link:
                listing.url = link.group(1)
                if not listing.url.startswith("http"):
                    listing.url = "https://www.inmuebles24.com" + listing.url

            price_m = re.search(r'\$[\s]*([\d,.]+)', card)
            if price_m:
                listing.price_mxn = int(re.sub(r"[^\d]", "", price_m.group(1)))

            area = re.search(r'(\d+(?:\.\d+)?)\s*m[²2]', card)
            if area:
                listing.area_m2 = float(area.group(1))
            beds = re.search(r'(\d+)\s*(?:rec[áa]mara|dorm)', card, re.IGNORECASE)
            if beds:
                listing.bedrooms = int(beds.group(1))

            listings.append(listing)
        return listings


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
