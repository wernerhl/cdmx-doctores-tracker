# Portal ToS may prohibit scraping. Output is for private research only.
# inmuebles24.com uses DataDome — expect intermittent blocks on datacenter IPs.

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

# JS to extract listing cards from inmuebles24 DOM
EXTRACT_CARDS_JS = """() => {
    const cards = document.querySelectorAll('[data-id][data-posting-type]');
    return Array.from(cards).map(card => {
        const text = card.innerText || '';
        const id = card.getAttribute('data-id') || '';
        const postingType = card.getAttribute('data-posting-type') || '';
        const detailUrl = card.getAttribute('data-to-posting') || '';

        // Price
        let price = null;
        const priceEl = card.querySelector('[data-qa="POSTING_CARD_PRICE"]') ||
                        card.querySelector('[class*="price"]');
        if (priceEl) {
            const pm = priceEl.innerText.replace(/[^\\d]/g, '');
            if (pm) price = parseInt(pm);
        }
        if (!price) {
            const pm2 = text.match(/\\$\\s*([\\d,]+)/);
            if (pm2) price = parseInt(pm2[1].replace(/,/g, ''));
        }

        // Area
        let area = null;
        const am = text.match(/(\\d+(?:\\.\\d+)?)\\s*m[²2]/);
        if (am) area = parseFloat(am[1]);

        // Bedrooms
        let beds = null;
        const bm = text.match(/(\\d+)\\s*(?:Rec[áa]mara|rec[áa]mara|Dorm|hab)/i);
        if (bm) beds = parseInt(bm[1]);

        // Bathrooms
        let baths = null;
        const btm = text.match(/(\\d+(?:\\.\\d+)?)\\s*(?:Ba[ñn]o|ba[ñn]o)/i);
        if (btm) baths = parseFloat(btm[1]);

        // Parking
        let parking = null;
        const pkm = text.match(/(\\d+)\\s*(?:Estac|estac)/i);
        if (pkm) parking = parseInt(pkm[1]);

        // Location
        let location = '';
        const locEl = card.querySelector('[data-qa="POSTING_CARD_LOCATION"]') ||
                       card.querySelector('[class*="location"]');
        if (locEl) location = locEl.innerText.trim();

        // Title
        let title = '';
        const titleEl = card.querySelector('[data-qa="POSTING_CARD_TITLE"]') ||
                         card.querySelector('h2, h3');
        if (titleEl) title = titleEl.innerText.trim();

        return { id, postingType, detailUrl, price, area, beds, baths, parking,
                 location, title, text: text.substring(0, 500) };
    });
}"""


class Inmuebles24Adapter(SourceAdapter):
    name = "inmuebles24"

    def build_search_urls(self) -> list[str]:
        urls = []
        price_min = self.config["price_min"]
        price_max = self.config["price_max"]
        # Use alcaldía-wide search (Cuauhtémoc) — colonia filtering in post-process
        for page_num in range(1, 8):
            if page_num == 1:
                url = (
                    f"https://www.inmuebles24.com/departamentos-en-venta-en-cuauhtemoc.html"
                    f"?precio-desde={price_min}&precio-hasta={price_max}"
                    f"&recamaras-desde=2"
                )
            else:
                url = (
                    f"https://www.inmuebles24.com/departamentos-en-venta-en-cuauhtemoc"
                    f"-pagina-{page_num}.html"
                    f"?precio-desde={price_min}&precio-hasta={price_max}"
                    f"&recamaras-desde=2"
                )
            urls.append(url)
        return urls

    async def fetch(self, browser_context, url: str) -> Optional[RawPage]:
        try:
            page = await browser_context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp is None or resp.status >= 400:
                logger.warning(f"[{self.name}] blocked HTTP {resp.status if resp else 'None'} for {url}")
                self.reachable = False
                await page.close()
                return None

            html = await page.content()
            if "captcha" in html.lower() or "datadome" in html.lower() or "geo.captcha" in html.lower():
                logger.warning(f"[{self.name}] DataDome captcha at {url}")
                self.reachable = False
                await page.close()
                return None

            # Extract cards via JS while page is live
            try:
                cards_data = await page.evaluate(EXTRACT_CARDS_JS)
            except Exception as e:
                logger.debug(f"[{self.name}] JS eval failed: {e}")
                cards_data = []

            await page.close()
            self._cache_page(url, html)

            raw_page = RawPage(url=url, html=html, status_code=resp.status)
            raw_page.json_data = {"cards": cards_data}
            return raw_page
        except Exception as e:
            logger.warning(f"[{self.name}] fetch error: {e}")
            self.reachable = False
            return None

    def parse_list(self, page: RawPage) -> list[RawListing]:
        listings = []

        # Use JS-extracted cards (most reliable)
        cards = (page.json_data or {}).get("cards", [])
        for card in cards:
            if not card.get("id"):
                continue
            listing = RawListing(source=self.name, listing_type="sale")
            listing.source_listing_id = str(card["id"])
            detail_url = card.get("detailUrl", "")
            if detail_url and not detail_url.startswith("http"):
                detail_url = "https://www.inmuebles24.com" + detail_url
            listing.url = detail_url
            listing.title = card.get("title", "")
            listing.description_raw = card.get("text", "")
            listing.price_mxn = card.get("price")
            listing.area_m2 = card.get("area")
            listing.bedrooms = card.get("beds")
            listing.bathrooms = card.get("baths")
            listing.parking = card.get("parking")
            listing.is_new_development = card.get("postingType") == "DEVELOPMENT"

            # Parse location: "Colonia, Alcaldía"
            loc = card.get("location", "")
            if "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                listing.colonia = parts[0]
                if len(parts) > 1:
                    listing.alcaldia = parts[-1]
            else:
                listing.colonia = loc
            listing.alcaldia = listing.alcaldia or "Cuauhtémoc"
            listing.address_raw = loc

            listings.append(listing)

        # Fallback: parse JSON-LD
        if not listings:
            listings = self._parse_json_ld(page.html)

        return listings

    def _parse_json_ld(self, html: str) -> list[RawListing]:
        listings = []
        json_lds = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        for jld in json_lds:
            try:
                data = json.loads(jld)
                if not isinstance(data, dict):
                    continue
                if data.get("@type") not in ("Apartment", "RealEstateListing"):
                    continue
                listing = RawListing(source=self.name, listing_type="sale")
                listing.url = data.get("url", "")
                listing.title = data.get("name", "")
                listing.description_raw = data.get("description", "")

                # Extract ID from URL
                id_m = re.search(r'-(\d{6,})\.html', listing.url)
                if id_m:
                    listing.source_listing_id = id_m.group(1)

                offers = data.get("offers", {})
                if isinstance(offers, dict) and offers.get("@type") != "AggregateOffer":
                    price_str = re.sub(r"[^\d]", "", str(offers.get("price", "")))
                    if price_str:
                        listing.price_mxn = int(price_str)

                fs = data.get("floorSize", {})
                if isinstance(fs, dict):
                    listing.area_m2 = _safe_float(fs.get("value"))

                listing.bedrooms = _safe_int(data.get("numberOfRooms"))
                listing.bathrooms = _safe_float(data.get("numberOfBathroomsTotal"))

                if listing.source_listing_id:
                    listings.append(listing)
            except (json.JSONDecodeError, KeyError):
                continue
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
