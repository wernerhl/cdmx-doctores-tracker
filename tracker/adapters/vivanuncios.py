# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)

# JS to extract listing cards from vivanuncios DOM
EXTRACT_CARDS_JS = """() => {
    const cards = document.querySelectorAll('[data-id]');
    return Array.from(cards).map(card => {
        const text = card.innerText || '';
        const id = card.getAttribute('data-id') || '';

        // Find detail link
        const links = card.querySelectorAll('a[href]');
        let detailUrl = '';
        for (const a of links) {
            if (a.href && (a.href.includes('/a-venta-') || a.href.includes('/d-desarrollo'))) {
                detailUrl = a.href;
                break;
            }
        }
        if (!detailUrl && links.length > 0) detailUrl = links[0].href;

        // Price — look for MN or $ prefix
        let price = null;
        const pm = text.match(/(?:MN|MXN|\\$)\\s*([\\d,]+(?:\\.\\d+)?)/);
        if (pm) price = parseInt(pm[1].replace(/[,\\.]/g, ''));

        // Area
        let area = null;
        const am = text.match(/(\\d+(?:\\.\\d+)?)\\s*m[²2]/);
        if (am) area = parseFloat(am[1]);

        // Bedrooms
        let beds = null;
        const bm = text.match(/(\\d+)\\s*rec/i);
        if (bm) beds = parseInt(bm[1]);

        // Bathrooms
        let baths = null;
        const btm = text.match(/(\\d+)\\s*ba[ñn]/i);
        if (btm) baths = parseFloat(btm[1]);

        // Parking
        let parking = null;
        const pkm = text.match(/(\\d+)\\s*estac/i);
        if (pkm) parking = parseInt(pkm[1]);

        // Location — last line-like text that looks like an address
        let location = '';
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 3);
        for (const line of lines) {
            if (line.match(/,/) && !line.match(/^[\\d\\$MN]/) && line.length < 100) {
                location = line;
            }
        }

        // Title
        let title = '';
        const h = card.querySelector('h2, h3, [class*="title"]');
        if (h) title = h.innerText.trim();

        return { id, detailUrl, price, area, beds, baths, parking, location, title,
                 text: text.substring(0, 600) };
    }).filter(c => c.id && c.id.length > 4);
}"""


class VivanunciosAdapter(SourceAdapter):
    name = "vivanuncios"

    def build_search_urls(self) -> list[str]:
        # Vivanuncios uses category codes in URLs
        # v1 = venta, c1294 = departamentos
        # Search across Cuauhtémoc — the site doesn't support per-colonia URL filtering
        # well (redirects to national), so we search broadly and filter in post-processing
        urls = []
        for page_num in range(1, 8):
            url = (
                f"https://www.vivanuncios.com.mx/s-departamentos-en-venta/"
                f"cuauhtemoc/v1c1294p{page_num}"
            )
            urls.append(url)
        return urls

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

            # Extract cards via JS
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

        # JS-extracted cards
        cards = (page.json_data or {}).get("cards", [])
        for card in cards:
            if not card.get("id"):
                continue
            listing = RawListing(source=self.name, listing_type="sale")
            listing.source_listing_id = str(card["id"])
            listing.url = card.get("detailUrl", "")
            listing.title = card.get("title", "")
            listing.description_raw = card.get("text", "")
            listing.price_mxn = card.get("price")
            listing.area_m2 = card.get("area")
            listing.bedrooms = card.get("beds")
            listing.bathrooms = card.get("baths")
            listing.parking = card.get("parking")

            loc = card.get("location", "")
            if "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                listing.colonia = parts[0]
                if len(parts) > 1:
                    listing.alcaldia = parts[-1]
            listing.address_raw = loc

            listings.append(listing)

        # Fallback: JSON-LD
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
                if data.get("@type") not in ("Product", "Apartment", "RealEstateListing"):
                    continue
                # Skip aggregate product entries
                if "Departamentos en venta" in data.get("name", ""):
                    continue

                listing = RawListing(source=self.name, listing_type="sale")
                listing.url = data.get("url", "")
                listing.title = data.get("name", "")
                listing.description_raw = data.get("description", "")

                id_m = re.search(r'/(\d{6,})', listing.url)
                if id_m:
                    listing.source_listing_id = id_m.group(1)

                offers = data.get("offers", {})
                if isinstance(offers, dict):
                    price_str = re.sub(r"[^\d]", "", str(offers.get("price", "")))
                    if price_str:
                        listing.price_mxn = int(price_str)

                if listing.source_listing_id:
                    listings.append(listing)
            except json.JSONDecodeError:
                continue
        return listings

    async def fetch_rental_listings(self, browser_context) -> list[RawListing]:
        urls = []
        for page_num in range(1, 4):
            url = (
                f"https://www.vivanuncios.com.mx/s-departamentos-en-renta/"
                f"cuauhtemoc/v1c1294p{page_num}"
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
