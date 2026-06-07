# Portal ToS may prohibit scraping. Output is for private research only.
# mudafy.com.mx is a SPA — prefer hitting the JSON API directly when discoverable.

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)


class MudafyAdapter(SourceAdapter):
    name = "mudafy"

    API_BASE = "https://www.mudafy.com.mx/api/properties"

    def build_search_urls(self) -> list[str]:
        urls = []
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        colonias = self.config.get("colonias", [])
        for col in colonias:
            colonia_name = col["name"].lower().replace(" ", "-").replace("á", "a").replace("í", "i").replace("é", "e")
            for page_num in range(1, 4):
                url = (
                    f"https://www.mudafy.com.mx/departamentos-en-venta/"
                    f"cuauhtemoc/{colonia_name}"
                    f"?precio_min={price_min}&precio_max={price_max}"
                    f"&recamaras_min=2&page={page_num}"
                )
                urls.append(url)
        return urls

    async def fetch(self, browser_context, url: str) -> Optional[RawPage]:
        api_url = self._to_api_url(url)
        if api_url:
            result = await self._fetch_api(browser_context, api_url)
            if result:
                return result

        return await super().fetch(browser_context, url)

    async def _fetch_api(self, browser_context, api_url: str) -> Optional[RawPage]:
        try:
            page = await browser_context.new_page()
            resp = await page.goto(api_url, wait_until="domcontentloaded", timeout=20000)
            if resp and resp.status == 200:
                body = await page.inner_text("body")
                await page.close()
                try:
                    data = json.loads(body)
                    self._cache_page(api_url, body)
                    return RawPage(url=api_url, html=body, json_data=data, status_code=200)
                except json.JSONDecodeError:
                    pass
            await page.close()
        except Exception as e:
            logger.debug(f"[{self.name}] API fetch failed: {e}")
        return None

    def _to_api_url(self, page_url: str) -> Optional[str]:
        m = re.search(r'cuauhtemoc/([\w-]+)', page_url)
        if not m:
            return None
        colonia = m.group(1)
        params = re.search(r'\?(.+)', page_url)
        qs = params.group(1) if params else ""
        return f"{self.API_BASE}?location=cuauhtemoc-{colonia}&type=apartment&operation=sale&{qs}"

    def parse_list(self, page: RawPage) -> list[RawListing]:
        if page.json_data:
            return self._from_api_response(page.json_data)
        return self._parse_dom(page.html)

    def _from_api_response(self, data) -> list[RawListing]:
        listings = []
        items = data if isinstance(data, list) else data.get("properties", data.get("results", []))
        for item in items:
            listing = RawListing(source=self.name, listing_type="sale")
            listing.source_listing_id = str(item.get("id", item.get("slug", "")))
            listing.url = item.get("url", "")
            if listing.url and not listing.url.startswith("http"):
                listing.url = "https://www.mudafy.com.mx" + listing.url
            listing.title = item.get("title", "")
            listing.description_raw = item.get("description", "")
            listing.price_mxn = _safe_int(item.get("price"))
            listing.area_m2 = _safe_float(item.get("area", item.get("totalArea")))
            listing.bedrooms = _safe_int(item.get("bedrooms"))
            listing.bathrooms = _safe_float(item.get("bathrooms"))
            listing.parking = _safe_int(item.get("parkingLots", item.get("parking")))
            listing.colonia = item.get("neighborhood", item.get("colonia", ""))
            listing.alcaldia = item.get("municipality", item.get("alcaldia", ""))
            listing.lat = _safe_float(item.get("latitude", item.get("lat")))
            listing.lon = _safe_float(item.get("longitude", item.get("lon")))
            listing.address_raw = item.get("address", "")
            if listing.source_listing_id:
                listings.append(listing)
        return listings

    def _parse_dom(self, html: str) -> list[RawListing]:
        listings = []

        next_data = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if next_data:
            try:
                data = json.loads(next_data.group(1))
                props = data.get("props", {}).get("pageProps", {})
                items = props.get("properties", props.get("listings", []))
                return self._from_api_response(items)
            except (json.JSONDecodeError, KeyError):
                pass

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
                    listing.url = "https://www.mudafy.com.mx" + listing.url

            id_match = re.search(r'/([\w-]{8,})', listing.url)
            if id_match:
                listing.source_listing_id = id_match.group(1)

            price_m = re.search(r'\$[\s]*([\d,]+)', card)
            if price_m:
                listing.price_mxn = int(re.sub(r"[^\d]", "", price_m.group(1)))

            area = re.search(r'(\d+(?:\.\d+)?)\s*m[²2]', card)
            if area:
                listing.area_m2 = float(area.group(1))

            if listing.source_listing_id:
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
