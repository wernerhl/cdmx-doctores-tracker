# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import hashlib
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fuzzy_address_match(addr1: Optional[str], addr2: Optional[str], threshold: int = 90) -> bool:
    if not addr1 or not addr2:
        return False
    try:
        from thefuzz import fuzz
        return fuzz.token_set_ratio(addr1.lower(), addr2.lower()) >= threshold
    except ImportError:
        a1 = set(addr1.lower().split())
        a2 = set(addr2.lower().split())
        if not a1 or not a2:
            return False
        overlap = len(a1 & a2)
        return overlap / max(len(a1), len(a2)) >= (threshold / 100)


def _relative_diff(a: float, b: float) -> float:
    if a == 0:
        return 0.0 if b == 0 else 1.0
    return abs(a - b) / abs(a)


def _canonical_uid(listing) -> str:
    key = f"{listing.colonia}|{listing.area_m2:.0f}|{listing.bedrooms}"
    if listing.lat and listing.lon:
        key += f"|{listing.lat:.5f}|{listing.lon:.5f}"
    elif listing.address_raw:
        key += f"|{listing.address_raw.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def deduplicate(listings: list, config: dict) -> list:
    dedup_cfg = config.get("dedup", {})
    distance_m = dedup_cfg.get("distance_m", 60)
    area_tol = dedup_cfg.get("area_tolerance", 0.05)
    price_tol = dedup_cfg.get("price_tolerance", 0.05)
    fuzzy_threshold = dedup_cfg.get("address_fuzzy_threshold", 90)

    clusters: list[list] = []

    for listing in listings:
        matched_cluster = None

        for cluster in clusters:
            rep = cluster[0]

            if listing.source == rep.source and listing.source_listing_id == rep.source_listing_id:
                matched_cluster = cluster
                break

            if rep.lat and rep.lon and listing.lat and listing.lon:
                dist = haversine_m(rep.lat, rep.lon, listing.lat, listing.lon)
                if (
                    dist < distance_m
                    and rep.area_m2 and listing.area_m2
                    and _relative_diff(rep.area_m2, listing.area_m2) < area_tol
                    and rep.bedrooms == listing.bedrooms
                    and rep.price_mxn and listing.price_mxn
                    and _relative_diff(rep.price_mxn, listing.price_mxn) < price_tol
                ):
                    matched_cluster = cluster
                    break
            elif (
                rep.area_m2 and listing.area_m2
                and _relative_diff(rep.area_m2, listing.area_m2) < area_tol
                and _fuzzy_address_match(rep.address_raw, listing.address_raw, fuzzy_threshold)
            ):
                matched_cluster = cluster
                break

        if matched_cluster is not None:
            matched_cluster.append(listing)
        else:
            clusters.append([listing])

    deduped = []
    for cluster in clusters:
        primary = _pick_primary(cluster)
        uid = primary.property_uid or _canonical_uid(primary)
        for item in cluster:
            item.property_uid = uid
        deduped.append(primary)

    logger.info(f"Dedup: {len(listings)} raw → {len(deduped)} unique properties ({len(clusters)} clusters)")
    return deduped


def _pick_primary(cluster: list):
    source_priority = {"icasas": 0, "lamudi": 1, "vivanuncios": 2, "inmuebles24": 3, "mudafy": 4, "propiedades": 5}

    def score(listing):
        completeness = sum([
            bool(listing.area_m2),
            bool(listing.bedrooms),
            bool(listing.bathrooms),
            bool(listing.lat and listing.lon),
            bool(listing.description_raw),
            bool(listing.address_raw),
        ])
        return (-completeness, source_priority.get(listing.source, 99))

    cluster.sort(key=score)
    primary = cluster[0]

    for other in cluster[1:]:
        if not primary.lat and other.lat:
            primary.lat = other.lat
            primary.lon = other.lon
        if not primary.address_raw and other.address_raw:
            primary.address_raw = other.address_raw
        if not primary.description_raw and other.description_raw:
            primary.description_raw = other.description_raw
        if not primary.has_elevator and other.has_elevator:
            primary.has_elevator = other.has_elevator
        if not primary.year_built and other.year_built:
            primary.year_built = other.year_built
        if not primary.floor and other.floor:
            primary.floor = other.floor

    return primary
