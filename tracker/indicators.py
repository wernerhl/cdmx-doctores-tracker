# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PropertyIndicators:
    price_per_m2: float = 0.0
    gross_annual_rent: float = 0.0
    gross_yield: float = 0.0
    gross_yield_lo: float = 0.0
    gross_yield_hi: float = 0.0
    effective_rent: float = 0.0
    annual_expenses: float = 0.0
    noi: float = 0.0
    cap_rate: float = 0.0
    grm: float = 0.0
    break_even_occ: Optional[float] = None
    spread_bp: Optional[float] = None
    discount_vs_colonia: float = 0.0
    cash_only: bool = False
    rent_source: str = "hedonic"
    suspicious_cheap: bool = False


def compute_indicators(listing, colonia_medians: dict, config: dict) -> PropertyIndicators:
    assumptions = config.get("assumptions", {})
    vacancy_weeks = assumptions.get("vacancy_weeks", 4)
    maint_month = assumptions.get("maintenance_mxn_month", 1500)
    predial_year = assumptions.get("predial_mxn_year", 1800)
    insurance_year = assumptions.get("insurance_mxn_year", 3500)
    repair_pct = assumptions.get("repair_reserve_pct", 0.01)
    mortgage_rate = assumptions.get("mortgage_rate_annual", 0.1045)

    ind = PropertyIndicators()
    ind.rent_source = listing.rent_source

    price = listing.price_mxn
    area = listing.area_m2

    if not price or not area or area == 0:
        return ind

    ind.price_per_m2 = price / area

    rent_hat = listing.rent_hat or 0
    rent_lo = listing.rent_hat_lo or 0
    rent_hi = listing.rent_hat_hi or 0

    if rent_hat <= 0:
        return ind

    ind.gross_annual_rent = rent_hat * 12
    gross_annual_lo = rent_lo * 12
    gross_annual_hi = rent_hi * 12

    ind.gross_yield = ind.gross_annual_rent / price if price > 0 else 0
    ind.gross_yield_lo = gross_annual_lo / price if price > 0 else 0
    ind.gross_yield_hi = gross_annual_hi / price if price > 0 else 0

    ind.effective_rent = ind.gross_annual_rent * (1 - vacancy_weeks / 52)

    ind.annual_expenses = (maint_month * 12) + predial_year + insurance_year + (price * repair_pct)

    ind.noi = ind.effective_rent - ind.annual_expenses
    ind.cap_rate = ind.noi / price if price > 0 else 0

    ind.grm = price / ind.gross_annual_rent if ind.gross_annual_rent > 0 else 0

    ind.cash_only = listing.is_recovery

    if not listing.is_recovery:
        annual_payment = price * mortgage_rate
        debt_service = annual_payment
        if ind.gross_annual_rent > 0:
            ind.break_even_occ = (ind.annual_expenses + debt_service) / ind.gross_annual_rent
        ind.spread_bp = (mortgage_rate - ind.cap_rate) * 10000

    colonia_median = colonia_medians.get(listing.colonia, {}).get("median_price_per_m2")
    if colonia_median and colonia_median > 0:
        ind.discount_vs_colonia = (ind.price_per_m2 / colonia_median) - 1

    if colonia_median and colonia_median > 0:
        if ind.price_per_m2 < 0.5 * colonia_median and not listing.is_recovery:
            ind.suspicious_cheap = True

    return ind


def compute_colonia_medians(listings: list) -> dict:
    from collections import defaultdict
    import statistics

    colonia_prices: dict[str, list[float]] = defaultdict(list)
    for listing in listings:
        if listing.quarantine_reason or not listing.price_mxn or not listing.area_m2:
            continue
        ppm2 = listing.price_mxn / listing.area_m2
        colonia_prices[listing.colonia].append(ppm2)

    medians = {}
    for colonia, prices in colonia_prices.items():
        if prices:
            medians[colonia] = {
                "median_price_per_m2": statistics.median(prices),
                "p25_price_per_m2": _percentile(prices, 25),
                "p75_price_per_m2": _percentile(prices, 75),
                "count": len(prices),
            }
    return medians


def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
