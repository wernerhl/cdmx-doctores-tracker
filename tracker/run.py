# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

logger = logging.getLogger("tracker")


def load_config(path: str = "tracker/config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


async def run_pipeline(run_date: date, config: dict) -> bool:
    from tracker.adapters import ADAPTERS
    from tracker.adapters.base import RawListing
    from tracker.normalize import normalize_raw, check_quarantine
    from tracker.geocode import geocode_listing
    from tracker.dedup import deduplicate
    from tracker.rent_model import should_refit, fit_hedonic, predict_rent, _load_latest_model
    from tracker.indicators import compute_indicators, compute_colonia_medians
    from tracker.state import (
        get_db, save_quarantined, upsert_listing, upsert_property,
        detect_changes, save_timeseries, export_timeseries_json,
        DB_PATH, DB_TEMP,
    )
    from tracker.analyze import score_and_rank, compute_market_aggregates
    from tracker.report import generate_daily_report, save_report, save_snapshot_csv
    from tracker.alert import check_and_send_alerts

    logger.info(f"=== Pipeline start: {run_date} ===")

    proxy_url = os.environ.get("PROXY_URL")
    enabled = config.get("sources_enabled", [])

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright not installed — run: pip install playwright && playwright install chromium")
        return False

    source_status: dict[str, bool] = {}
    all_sale_raw: list[RawListing] = []
    all_rent_raw: list[RawListing] = []

    async with async_playwright() as pw:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}

        browser = await pw.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )

        try:
            from playwright_stealth import stealth_async
            await stealth_async(context)
        except ImportError:
            logger.warning("playwright-stealth not installed — proceeding without stealth")

        for source_name in enabled:
            if source_name not in ADAPTERS:
                logger.warning(f"Unknown source: {source_name}")
                source_status[source_name] = False
                continue

            adapter_cls = ADAPTERS[source_name]
            adapter = adapter_cls(config, run_date)

            if not adapter.healthcheck():
                logger.info(f"[{source_name}] skipped (not reachable)")
                source_status[source_name] = False
                continue

            logger.info(f"[{source_name}] fetching sale listings...")
            try:
                sales = await adapter.fetch_all_listings(context)
                all_sale_raw.extend(sales)
                source_status[source_name] = adapter.reachable
                logger.info(f"[{source_name}] {len(sales)} sale listings, reachable={adapter.reachable}")
            except Exception as e:
                logger.error(f"[{source_name}] sale fetch failed: {e}")
                source_status[source_name] = False

            if adapter.reachable:
                logger.info(f"[{source_name}] fetching rental listings...")
                try:
                    rentals = await adapter.fetch_rental_listings(context)
                    all_rent_raw.extend(rentals)
                    logger.info(f"[{source_name}] {len(rentals)} rental listings")
                except Exception as e:
                    logger.warning(f"[{source_name}] rental fetch failed: {e}")

        await browser.close()

    live_count = sum(1 for v in source_status.values() if v)
    logger.info(f"Sources: {live_count}/{len(enabled)} live, {len(all_sale_raw)} raw sales, {len(all_rent_raw)} raw rentals")

    if len(all_sale_raw) == 0:
        logger.error("ALL sources returned 0 listings — aborting to preserve yesterday's data")
        return False

    logger.info("Normalizing listings...")
    sale_listings = [normalize_raw(r, run_date, config) for r in all_sale_raw]
    rent_listings = [normalize_raw(r, run_date, config) for r in all_rent_raw]
    for r in rent_listings:
        r.listing_type = "rent"

    logger.info("Geocoding...")
    for listing in sale_listings + rent_listings:
        geocode_listing(listing)

    quarantine_count = 0
    clean_sales = []
    for listing in sale_listings:
        reason = check_quarantine(listing, config)
        if reason:
            listing.quarantine_reason = reason
            quarantine_count += 1
        clean_sales.append(listing)

    logger.info(f"Quarantined: {quarantine_count}")

    logger.info("Deduplicating...")
    deduped = deduplicate(
        [l for l in clean_sales if not l.quarantine_reason],
        config,
    )
    for l in clean_sales:
        if l.quarantine_reason:
            deduped.append(l)

    logger.info("Building rent model...")
    if should_refit(run_date, config):
        model = fit_hedonic(rent_listings, run_date, config)
    else:
        model = _load_latest_model(run_date) or {}

    fallback_count = 0
    for listing in deduped:
        if listing.quarantine_reason or listing.listing_type != "sale":
            continue
        rent_hat, rent_lo, rent_hi, rent_src = predict_rent(listing, model, config)
        listing.rent_hat = rent_hat
        listing.rent_hat_lo = rent_lo
        listing.rent_hat_hi = rent_hi
        listing.rent_source = rent_src
        if rent_src == "fallback":
            fallback_count += 1

    logger.info(f"Rent predictions: {fallback_count} on fallback")

    logger.info("Persisting state...")
    if Path(DB_PATH).exists():
        shutil.copy2(DB_PATH, DB_TEMP)
    db = get_db(DB_TEMP if Path(DB_TEMP).exists() else DB_PATH)

    for listing in deduped:
        if listing.quarantine_reason:
            save_quarantined(db, listing, listing.quarantine_reason, run_date)
        else:
            upsert_listing(db, listing)
            upsert_property(db, listing)

    db.commit()

    logger.info("Detecting changes...")
    active_sales = [l for l in deduped if not l.quarantine_reason and l.listing_type == "sale"]
    changes = detect_changes(db, active_sales, run_date, config)
    db.commit()

    logger.info(f"Changes: new={len(changes['new'])}, drops={len(changes['price_drop'])}, "
                f"sold={len(changes['presumed_sold'])}")

    logger.info("Scoring and ranking...")
    regular, recovery = score_and_rank(deduped, config)

    indicators_map = {}
    for sp in regular + recovery:
        indicators_map[sp.listing.property_uid] = sp.indicators

    logger.info("Computing aggregates...")
    aggregates = compute_market_aggregates(active_sales, indicators_map, changes, config)
    save_timeseries(db, aggregates, run_date)
    db.commit()

    export_timeseries_json(db)

    # Also copy timeseries to docs/ for GitHub Pages dashboard
    docs_data = Path("docs/data")
    if docs_data.exists():
        shutil.copy2("data/market_timeseries.json", docs_data / "market_timeseries.json")
        logger.info("Copied timeseries to docs/data/ for dashboard")

    if Path(DB_TEMP).exists():
        shutil.move(DB_TEMP, DB_PATH)
        logger.info("Atomic DB swap complete")

    db.close()

    logger.info("Generating report...")
    report = generate_daily_report(
        run_date=run_date,
        regular=regular,
        recovery=recovery,
        changes=changes,
        aggregates=aggregates,
        source_status=source_status,
        model_info=model,
        quarantine_count=quarantine_count,
        fallback_count=fallback_count,
        total_count=len(active_sales),
        config=config,
    )
    save_report(report, run_date)
    save_snapshot_csv(deduped, indicators_map, run_date)

    check_and_send_alerts(regular, changes, config)

    logger.info(f"=== Pipeline complete: {run_date} ===")
    return True


def main():
    parser = argparse.ArgumentParser(description="CDMX Doctores Listing Tracker")
    parser.add_argument("--date", type=str, default=None, help="Run date (YYYY-MM-DD)")
    parser.add_argument("--config", type=str, default="tracker/config.yaml", help="Config path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(f"tracker_run.log"),
        ],
    )

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    config = load_config(args.config)

    success = asyncio.run(run_pipeline(run_date, config))
    if not success:
        logger.error("Pipeline failed — exiting non-zero")
        sys.exit(1)


if __name__ == "__main__":
    main()
