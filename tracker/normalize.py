# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

RECOVERY_PATTERNS = [
    (r"remate", "remate"),
    (r"recuperaci[óo]n\s+(?:bancaria|hipotecaria)", "recuperacion_bancaria"),
    (r"cesi[óo]n\s+de\s+derechos", "cesion_derechos"),
    (r"no\s+se\s+aceptan?\s+cr[ée]dito", "no_credito"),
    (r"pago\s+de\s+contado", "pago_contado"),
    (r"desalojo", "desalojo_pendiente"),
    (r"en\s+demanda", "en_demanda"),
    (r"sentencia\s+en\s+firme", "sentencia_firme"),
]

DELIVERY_PATTERN = re.compile(r"(\d+)[\s-]*(?:a\s*)?(\d+)?\s*meses", re.IGNORECASE)


@dataclass
class Listing:
    source: str = ""
    source_listing_id: str = ""
    url: str = ""
    title: str = ""
    description_raw: str = ""
    price_mxn: int = 0
    area_m2: float = 0.0
    bedrooms: int = 0
    bathrooms: float = 0.0
    parking: int = 0
    floor: Optional[str] = None
    has_elevator: Optional[bool] = None
    year_built: Optional[int] = None
    colonia: str = ""
    alcaldia: str = ""
    address_raw: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_new_development: bool = False
    is_recovery: bool = False
    recovery_flags: list[str] = field(default_factory=list)
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    run_date: Optional[date] = None
    property_uid: Optional[str] = None
    listing_type: str = "sale"
    rent_hat: Optional[float] = None
    rent_hat_lo: Optional[float] = None
    rent_hat_hi: Optional[float] = None
    rent_source: str = "hedonic"
    quarantine_reason: Optional[str] = None


def normalize_raw(raw, run_date: date, config: dict) -> Listing:
    from tracker.adapters.base import RawListing

    listing = Listing(
        source=raw.source,
        source_listing_id=raw.source_listing_id,
        url=raw.url,
        title=raw.title or "",
        description_raw=raw.description_raw or "",
        price_mxn=raw.price_mxn or 0,
        area_m2=raw.area_m2 or 0.0,
        bedrooms=raw.bedrooms or 0,
        bathrooms=raw.bathrooms or 0.0,
        parking=raw.parking or 0,
        floor=raw.floor,
        has_elevator=raw.has_elevator,
        year_built=raw.year_built,
        colonia=_normalize_colonia(raw.colonia),
        alcaldia=raw.alcaldia or "Cuauhtémoc",
        address_raw=raw.address_raw,
        lat=raw.lat,
        lon=raw.lon,
        is_new_development=raw.is_new_development,
        run_date=run_date,
        first_seen=run_date,
        last_seen=run_date,
        listing_type=getattr(raw, "listing_type", "sale"),
    )

    text = f"{listing.title} {listing.description_raw}"
    _detect_recovery(listing, text)
    _extract_missing_features(listing, text)
    _detect_new_development(listing, text)

    return listing


def _normalize_colonia(name: str) -> str:
    if not name:
        return ""
    name = name.strip()
    mapping = {
        "doctores": "Doctores",
        "obrera": "Obrera",
        "algarin": "Algarín",
        "algarín": "Algarín",
        "buenos aires": "Buenos Aires",
        "centro": "Centro",
        "centro historico": "Centro",
        "centro histórico": "Centro",
        "guerrero": "Guerrero",
        "roma sur": "Roma Sur",
        "santa maria la ribera": "Santa María la Ribera",
        "santa maría la ribera": "Santa María la Ribera",
        "sta maria la ribera": "Santa María la Ribera",
    }
    return mapping.get(name.lower(), name)


def _detect_recovery(listing: Listing, text: str):
    text_lower = text.lower()
    flags = []
    for pattern, flag in RECOVERY_PATTERNS:
        if re.search(pattern, text_lower):
            flags.append(flag)

    delivery = DELIVERY_PATTERN.search(text_lower)
    if delivery:
        lo = delivery.group(1)
        hi = delivery.group(2) or lo
        flags.append(f"entrega_{lo}_{hi}m")

    if flags:
        listing.is_recovery = True
        listing.recovery_flags = flags


def _extract_missing_features(listing: Listing, text: str):
    if not listing.area_m2:
        m = re.search(r'(\d+(?:\.\d+)?)\s*m[²2]', text)
        if m:
            listing.area_m2 = float(m.group(1))

    if not listing.bedrooms:
        m = re.search(r'(\d+)\s*(?:rec[áa]mara|dormitorio|hab)', text, re.IGNORECASE)
        if m:
            listing.bedrooms = int(m.group(1))

    if not listing.bathrooms:
        m = re.search(r'(\d+(?:\.\d+)?)\s*ba[ñn]o', text, re.IGNORECASE)
        if m:
            listing.bathrooms = float(m.group(1))

    if not listing.parking:
        m = re.search(r'(\d+)\s*estacionamiento', text, re.IGNORECASE)
        if m:
            listing.parking = int(m.group(1))

    if listing.floor is None:
        m = re.search(r'piso\s+(\d+|pb|planta\s+baja)', text, re.IGNORECASE)
        if m:
            val = m.group(1).upper()
            listing.floor = "PB" if "BAJA" in val or val == "PB" else val

    if listing.has_elevator is None:
        if re.search(r'elevador|ascensor', text, re.IGNORECASE):
            listing.has_elevator = True

    if listing.year_built is None:
        m = re.search(r'(?:antigüedad|construido?\s+en|año)\s*:?\s*(\d{4})', text, re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1900 <= year <= 2030:
                listing.year_built = year


def _detect_new_development(listing: Listing, text: str):
    if re.search(r'preventa|pre-venta|nuevo\s+desarrollo|estrenar', text, re.IGNORECASE):
        listing.is_new_development = True


def check_quarantine(listing: Listing, config: dict) -> Optional[str]:
    q = config.get("quarantine", {})
    if listing.area_m2 and (listing.area_m2 < q.get("area_min", 20) or listing.area_m2 > q.get("area_max", 200)):
        return f"area_out_of_range:{listing.area_m2}"
    if listing.price_mxn and (listing.price_mxn < q.get("price_min", 400000) or listing.price_mxn > q.get("price_max", 6000000)):
        return f"price_out_of_range:{listing.price_mxn}"
    if listing.bedrooms and (listing.bedrooms < q.get("bedrooms_min", 0) or listing.bedrooms > q.get("bedrooms_max", 6)):
        return f"bedrooms_out_of_range:{listing.bedrooms}"
    return None
