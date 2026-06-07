# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_daily_report(
    run_date: date,
    regular: list,
    recovery: list,
    changes: dict,
    aggregates: list,
    source_status: dict,
    model_info: dict,
    quarantine_count: int,
    fallback_count: int,
    total_count: int,
    config: dict,
) -> str:
    lines = []
    lines.append(f"# CDMX Doctores Market Report — {run_date}")
    lines.append("")

    total_agg = next((a for a in aggregates if a["colonia"] == "_TOTAL"), {})
    n_active = total_agg.get("n_active", total_count)
    med_ppm2 = total_agg.get("median_price_per_m2", 0)
    med_yield = total_agg.get("median_gross_yield", 0)
    new_count = len(changes.get("new", []))
    sold_count = len(changes.get("presumed_sold", []))

    lines.append("## Market Summary")
    lines.append("")
    lines.append(f"- **{n_active}** active properties across target colonias")
    lines.append(f"- Median price/m²: **${med_ppm2:,.0f}** MXN")
    lines.append(f"- Median gross yield: **{med_yield*100:.1f}%**")
    lines.append(f"- Today: **{new_count}** new, **{sold_count}** presumed sold")
    lines.append("")

    lines.append("## Top 10 Opportunities")
    lines.append("")
    top10 = regular[:10]
    if top10:
        lines.append("| # | Colonia | Price | Area | $/m² | Yield | Yield Band | Discount | Score | Link |")
        lines.append("|---|---------|-------|------|------|-------|------------|----------|-------|------|")
        for sp in top10:
            l = sp.listing
            ind = sp.indicators
            yield_band = f"{ind.gross_yield_lo*100:.1f}–{ind.gross_yield_hi*100:.1f}%"
            discount = f"{ind.discount_vs_colonia*100:+.1f}%"
            suspicious = " ⚠️" if ind.suspicious_cheap else ""
            fallback_flag = " [F]" if ind.rent_source == "fallback" else ""
            lines.append(
                f"| {sp.rank} | {l.colonia} | ${l.price_mxn:,} | {l.area_m2:.0f}m² | "
                f"${ind.price_per_m2:,.0f} | {ind.gross_yield*100:.1f}%{fallback_flag} | "
                f"{yield_band} | {discount}{suspicious} | {sp.composite_score:.2f} | "
                f"[link]({l.url}) |"
            )
        lines.append("")
    else:
        lines.append("*No listings to rank.*")
        lines.append("")

    drops = changes.get("price_drop", [])
    if drops:
        lines.append("## Price Drops Today")
        lines.append("")
        lines.append("| Colonia | Old Price | New Price | Δ MXN | Δ bp | Link |")
        lines.append("|---------|-----------|-----------|-------|------|------|")
        sorted_drops = sorted(drops, key=lambda x: x[2])
        for listing, change_mxn, change_bp in sorted_drops:
            old = listing.price_mxn - change_mxn
            lines.append(
                f"| {listing.colonia} | ${old:,} | ${listing.price_mxn:,} | "
                f"${change_mxn:,} | {change_bp:,} | [link]({listing.url}) |"
            )
        lines.append("")

    rises = changes.get("price_rise", [])
    if rises:
        lines.append("## Price Rises Today")
        lines.append("")
        lines.append(f"*{len(rises)} listing(s) increased in price.*")
        lines.append("")

    if recovery:
        lines.append("## Recovery Watch (High-Diligence Required)")
        lines.append("")
        lines.append("| # | Colonia | All-In Price | Area | Flags | Delivery | Score | Link |")
        lines.append("|---|---------|-------------|------|-------|----------|-------|------|")
        for sp in recovery[:10]:
            l = sp.listing
            flags = ", ".join(l.recovery_flags) if l.recovery_flags else "—"
            delivery = ""
            for f in (l.recovery_flags or []):
                if f.startswith("entrega_"):
                    delivery = f.replace("entrega_", "").replace("_", "–") + " meses"
            lines.append(
                f"| {sp.rank} | {l.colonia} | ${l.price_mxn:,} | {l.area_m2:.0f}m² | "
                f"{flags} | {delivery or '—'} | {sp.composite_score:.2f} | [link]({l.url}) |"
            )
        lines.append("")
        lines.append("*Recovery listings: cash_only=True, no mortgage metrics computed.*")
        lines.append("")

    bom = changes.get("back_on_market", [])
    if bom:
        lines.append("## Back on Market")
        lines.append("")
        for l in bom:
            lines.append(f"- {l.colonia} — ${l.price_mxn:,} — [link]({l.url})")
        lines.append("")

    col_aggs = [a for a in aggregates if a["colonia"] != "_TOTAL"]
    if col_aggs:
        lines.append("## Colonia Breakdown")
        lines.append("")
        lines.append("| Colonia | Active | Med $/m² | Med Yield | New | Sold |")
        lines.append("|---------|--------|----------|-----------|-----|------|")
        for a in sorted(col_aggs, key=lambda x: x["colonia"]):
            lines.append(
                f"| {a['colonia']} | {a['n_active']} | ${a['median_price_per_m2']:,.0f} | "
                f"{a['median_gross_yield']*100:.1f}% | {a['new_count']} | {a['presumed_sold_count']} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Data Quality")
    lines.append("")

    live_sources = [s for s, ok in source_status.items() if ok]
    blocked_sources = [s for s, ok in source_status.items() if not ok]
    lines.append(f"Sources live: {', '.join(live_sources) or 'none'}. "
                 f"Blocked: {', '.join(blocked_sources) or 'none'}.")

    n_model = model_info.get("n", 0)
    r2 = model_info.get("r_squared", 0)
    lines.append(
        f"{n_active} active properties ({new_count} new, {sold_count} presumed sold). "
        f"Yields use hedonic rent (n={n_model}, R²={r2:.2f}); "
        f"{fallback_count} listings on fallback rent."
    )
    if quarantine_count:
        lines.append(f"{quarantine_count} listings quarantined (out-of-range values).")
    lines.append("")
    lines.append("*Portal ToS may prohibit scraping. Output is for private research only.*")

    return "\n".join(lines)


def save_report(report: str, run_date: date) -> str:
    path = Path("reports") / f"daily_{run_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(report)
    logger.info(f"Report saved to {path}")
    return str(path)


def save_snapshot_csv(listings: list, indicators_map: dict, run_date: date) -> str:
    import csv
    path = Path("data") / f"snapshot_{run_date}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "property_uid", "source", "source_listing_id", "url", "colonia", "alcaldia",
        "price_mxn", "area_m2", "bedrooms", "bathrooms", "parking", "floor",
        "has_elevator", "year_built", "lat", "lon",
        "is_new_development", "is_recovery", "recovery_flags",
        "first_seen", "last_seen",
        "price_per_m2", "gross_yield", "gross_yield_lo", "gross_yield_hi",
        "cap_rate", "grm", "discount_vs_colonia",
        "rent_hat", "rent_source", "suspicious_cheap",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for listing in listings:
            if listing.quarantine_reason or listing.listing_type != "sale":
                continue
            ind = indicators_map.get(listing.property_uid)
            row = {
                "property_uid": listing.property_uid,
                "source": listing.source,
                "source_listing_id": listing.source_listing_id,
                "url": listing.url,
                "colonia": listing.colonia,
                "alcaldia": listing.alcaldia,
                "price_mxn": listing.price_mxn,
                "area_m2": listing.area_m2,
                "bedrooms": listing.bedrooms,
                "bathrooms": listing.bathrooms,
                "parking": listing.parking,
                "floor": listing.floor,
                "has_elevator": listing.has_elevator,
                "year_built": listing.year_built,
                "lat": listing.lat,
                "lon": listing.lon,
                "is_new_development": listing.is_new_development,
                "is_recovery": listing.is_recovery,
                "recovery_flags": ",".join(listing.recovery_flags) if listing.recovery_flags else "",
                "first_seen": listing.first_seen,
                "last_seen": listing.last_seen,
            }
            if ind:
                row.update({
                    "price_per_m2": round(ind.price_per_m2, 2),
                    "gross_yield": round(ind.gross_yield, 4),
                    "gross_yield_lo": round(ind.gross_yield_lo, 4),
                    "gross_yield_hi": round(ind.gross_yield_hi, 4),
                    "cap_rate": round(ind.cap_rate, 4),
                    "grm": round(ind.grm, 2),
                    "discount_vs_colonia": round(ind.discount_vs_colonia, 4),
                    "rent_hat": round(listing.rent_hat, 2) if listing.rent_hat else "",
                    "rent_source": listing.rent_source,
                    "suspicious_cheap": ind.suspicious_cheap,
                })
            writer.writerow(row)

    logger.info(f"Snapshot CSV saved to {path}")
    return str(path)
