# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import unicodedata
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

GEOCODE_DB = "cache/geocode.sqlite"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "CDMXListingTracker/1.0 (private research)"


def _normalize_address(address: str) -> str:
    addr = unicodedata.normalize("NFKD", address.lower())
    addr = re.sub(r"[^\w\s,]", "", addr)
    addr = re.sub(r"\s+", " ", addr).strip()
    return addr


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(GEOCODE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address_normalized TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            source TEXT,
            cached_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def geocode_address(address: str, colonia: str = "", alcaldia: str = "") -> Tuple[Optional[float], Optional[float]]:
    if not address:
        return None, None

    normalized = _normalize_address(address)
    if not normalized:
        return None, None

    db = _get_db()
    row = db.execute(
        "SELECT lat, lon FROM geocode_cache WHERE address_normalized = ?",
        (normalized,)
    ).fetchone()
    if row:
        db.close()
        return row[0], row[1]

    lat, lon = _geocode_nominatim(address, colonia, alcaldia)

    db.execute(
        "INSERT OR REPLACE INTO geocode_cache (address_normalized, lat, lon, source) VALUES (?, ?, ?, ?)",
        (normalized, lat, lon, "nominatim" if lat else "failed")
    )
    db.commit()
    db.close()

    return lat, lon


def _geocode_nominatim(address: str, colonia: str, alcaldia: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        import urllib.request
        import urllib.parse
        import json

        query_parts = [address]
        if colonia:
            query_parts.append(colonia)
        if alcaldia:
            query_parts.append(alcaldia)
        query_parts.append("Ciudad de México, México")
        query = ", ".join(query_parts)

        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "mx",
        })
        url = f"{NOMINATIM_URL}?{params}"

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        time.sleep(1.1)

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.debug(f"Nominatim geocode failed for '{address}': {e}")

    return None, None


def geocode_listing(listing) -> None:
    if listing.lat and listing.lon:
        return

    address = listing.address_raw or listing.title
    if not address:
        return

    lat, lon = geocode_address(address, listing.colonia, listing.alcaldia)
    if lat and lon:
        listing.lat = lat
        listing.lon = lon
