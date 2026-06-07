# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from tracker.indicators import PropertyIndicators, compute_indicators, compute_colonia_medians

logger = logging.getLogger(__name__)


@dataclass
class ScoredProperty:
    listing: object
    indicators: PropertyIndicators
    composite_score: float = 0.0
    rank: int = 0


def score_and_rank(listings: list, config: dict) -> tuple[list[ScoredProperty], list[ScoredProperty]]:
    colonia_medians = compute_colonia_medians(listings)

    regular = []
    recovery = []

    for listing in listings:
        if listing.quarantine_reason:
            continue
        if listing.listing_type != "sale":
            continue

        ind = compute_indicators(listing, colonia_medians, config)
        sp = ScoredProperty(listing=listing, indicators=ind)

        if listing.is_recovery:
            recovery.append(sp)
        else:
            regular.append(sp)

    _compute_composite_scores(regular, config)
    _compute_composite_scores(recovery, config)

    regular.sort(key=lambda x: -x.composite_score)
    recovery.sort(key=lambda x: -x.composite_score)

    for i, sp in enumerate(regular):
        sp.rank = i + 1
    for i, sp in enumerate(recovery):
        sp.rank = i + 1

    return regular, recovery


def _compute_composite_scores(scored: list[ScoredProperty], config: dict):
    if not scored:
        return

    weights = config.get("scoring_weights", {})
    w_price = weights.get("neg_price_per_m2", 0.3)
    w_yield = weights.get("gross_yield", 0.3)
    w_grm = weights.get("neg_grm", 0.2)
    w_discount = weights.get("discount_vs_colonia", 0.2)

    price_per_m2_vals = [sp.indicators.price_per_m2 for sp in scored if sp.indicators.price_per_m2 > 0]
    yield_vals = [sp.indicators.gross_yield for sp in scored if sp.indicators.gross_yield > 0]
    grm_vals = [sp.indicators.grm for sp in scored if sp.indicators.grm > 0]
    discount_vals = [sp.indicators.discount_vs_colonia for sp in scored]

    stats = {
        "price_per_m2": _stats(price_per_m2_vals),
        "gross_yield": _stats(yield_vals),
        "grm": _stats(grm_vals),
        "discount": _stats(discount_vals),
    }

    for sp in scored:
        z_price = _zscore(-sp.indicators.price_per_m2, stats["price_per_m2"])
        z_yield = _zscore(sp.indicators.gross_yield, stats["gross_yield"])
        z_grm = _zscore(-sp.indicators.grm, stats["grm"])
        z_discount = _zscore(-sp.indicators.discount_vs_colonia, stats["discount"])

        sp.composite_score = (
            w_price * z_price +
            w_yield * z_yield +
            w_grm * z_grm +
            w_discount * z_discount
        )


def _stats(vals: list[float]) -> tuple[float, float]:
    if len(vals) < 2:
        return (0.0, 1.0)
    return (statistics.mean(vals), statistics.stdev(vals) or 1.0)


def _zscore(val: float, stats: tuple[float, float]) -> float:
    mean, std = stats
    return (val - mean) / std


def compute_market_aggregates(
    listings: list,
    indicators_map: dict[str, PropertyIndicators],
    changes: dict,
    config: dict,
) -> list[dict]:
    colonia_listings: dict[str, list] = defaultdict(list)
    for listing in listings:
        if listing.quarantine_reason or listing.listing_type != "sale":
            continue
        colonia_listings[listing.colonia].append(listing)

    new_by_colonia = defaultdict(int)
    for l in changes.get("new", []):
        new_by_colonia[l.colonia] += 1

    sold_by_colonia = defaultdict(int)
    for uid in changes.get("presumed_sold", []):
        for l in listings:
            if l.property_uid == uid:
                sold_by_colonia[l.colonia] += 1
                break

    aggregates = []
    all_listings_flat = []

    for colonia, col_listings in colonia_listings.items():
        ppm2 = [l.price_mxn / l.area_m2 for l in col_listings if l.price_mxn and l.area_m2]
        prices = [l.price_mxn for l in col_listings if l.price_mxn]
        yields_list = []
        dom_list = []

        for l in col_listings:
            ind = indicators_map.get(l.property_uid)
            if ind and ind.gross_yield > 0:
                yields_list.append(ind.gross_yield)
            if l.first_seen and l.last_seen:
                from datetime import date as dt
                fs = l.first_seen if isinstance(l.first_seen, dt) else dt.fromisoformat(str(l.first_seen))
                ls = l.last_seen if isinstance(l.last_seen, dt) else dt.fromisoformat(str(l.last_seen))
                dom_list.append((ls - fs).days)

        agg = {
            "colonia": colonia,
            "n_active": len(col_listings),
            "median_price_per_m2": statistics.median(ppm2) if ppm2 else 0,
            "p25_price_per_m2": _percentile(ppm2, 25),
            "p75_price_per_m2": _percentile(ppm2, 75),
            "median_gross_yield": statistics.median(yields_list) if yields_list else 0,
            "median_days_on_market": statistics.median(dom_list) if dom_list else 0,
            "new_count": new_by_colonia.get(colonia, 0),
            "presumed_sold_count": sold_by_colonia.get(colonia, 0),
            "median_price": int(statistics.median(prices)) if prices else 0,
        }
        aggregates.append(agg)
        all_listings_flat.extend(col_listings)

    all_ppm2 = [l.price_mxn / l.area_m2 for l in all_listings_flat if l.price_mxn and l.area_m2]
    all_prices = [l.price_mxn for l in all_listings_flat if l.price_mxn]
    all_yields = []
    for l in all_listings_flat:
        ind = indicators_map.get(l.property_uid)
        if ind and ind.gross_yield > 0:
            all_yields.append(ind.gross_yield)

    total_agg = {
        "colonia": "_TOTAL",
        "n_active": len(all_listings_flat),
        "median_price_per_m2": statistics.median(all_ppm2) if all_ppm2 else 0,
        "p25_price_per_m2": _percentile(all_ppm2, 25),
        "p75_price_per_m2": _percentile(all_ppm2, 75),
        "median_gross_yield": statistics.median(all_yields) if all_yields else 0,
        "median_days_on_market": 0,
        "new_count": sum(new_by_colonia.values()),
        "presumed_sold_count": sum(sold_by_colonia.values()),
        "median_price": int(statistics.median(all_prices)) if all_prices else 0,
    }
    aggregates.append(total_agg)

    return aggregates


def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])
