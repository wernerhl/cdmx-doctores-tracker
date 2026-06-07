# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "tracker.sqlite"
DB_TEMP = "tracker.sqlite.tmp"


def get_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings_current (
            property_uid TEXT NOT NULL,
            source TEXT NOT NULL,
            source_listing_id TEXT NOT NULL,
            url TEXT,
            title TEXT,
            description_raw TEXT,
            price_mxn INTEGER,
            area_m2 REAL,
            bedrooms INTEGER,
            bathrooms REAL,
            parking INTEGER,
            floor TEXT,
            has_elevator INTEGER,
            year_built INTEGER,
            colonia TEXT,
            alcaldia TEXT,
            address_raw TEXT,
            lat REAL,
            lon REAL,
            is_new_development INTEGER DEFAULT 0,
            is_recovery INTEGER DEFAULT 0,
            recovery_flags TEXT,
            first_seen TEXT,
            last_seen TEXT,
            run_date TEXT,
            rent_hat REAL,
            rent_hat_lo REAL,
            rent_hat_hi REAL,
            rent_source TEXT DEFAULT 'hedonic',
            listing_type TEXT DEFAULT 'sale',
            PRIMARY KEY (property_uid, source)
        );

        CREATE TABLE IF NOT EXISTS listings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_uid TEXT NOT NULL,
            source TEXT NOT NULL,
            source_listing_id TEXT NOT NULL,
            price_mxn INTEGER,
            run_date TEXT NOT NULL,
            event TEXT,
            price_change_mxn INTEGER,
            price_change_bp INTEGER
        );

        CREATE TABLE IF NOT EXISTS properties (
            property_uid TEXT PRIMARY KEY,
            canonical_address TEXT,
            colonia TEXT,
            area_m2 REAL,
            bedrooms INTEGER,
            first_seen TEXT,
            last_seen TEXT,
            status TEXT DEFAULT 'active',
            days_on_market INTEGER DEFAULT 0,
            source_count INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS market_timeseries (
            run_date TEXT NOT NULL,
            colonia TEXT NOT NULL,
            n_active INTEGER,
            median_price_per_m2 REAL,
            p25_price_per_m2 REAL,
            p75_price_per_m2 REAL,
            median_gross_yield REAL,
            median_days_on_market REAL,
            new_count INTEGER,
            presumed_sold_count INTEGER,
            median_price INTEGER,
            PRIMARY KEY (run_date, colonia)
        );

        CREATE TABLE IF NOT EXISTS quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            source_listing_id TEXT,
            url TEXT,
            price_mxn INTEGER,
            area_m2 REAL,
            bedrooms INTEGER,
            reason TEXT,
            run_date TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_current_uid ON listings_current(property_uid);
        CREATE INDEX IF NOT EXISTS idx_current_run ON listings_current(run_date);
        CREATE INDEX IF NOT EXISTS idx_history_uid ON listings_history(property_uid);
        CREATE INDEX IF NOT EXISTS idx_history_date ON listings_history(run_date);
        CREATE INDEX IF NOT EXISTS idx_ts_date ON market_timeseries(run_date);
    """)


def save_quarantined(conn: sqlite3.Connection, listing, reason: str, run_date: date):
    conn.execute(
        "INSERT INTO quarantine (source, source_listing_id, url, price_mxn, area_m2, bedrooms, reason, run_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (listing.source, listing.source_listing_id, listing.url,
         listing.price_mxn, listing.area_m2, listing.bedrooms, reason, str(run_date))
    )


def upsert_listing(conn: sqlite3.Connection, listing):
    existing = conn.execute(
        "SELECT first_seen FROM listings_current WHERE property_uid = ? AND source = ?",
        (listing.property_uid, listing.source)
    ).fetchone()

    first_seen = existing["first_seen"] if existing else str(listing.first_seen)

    conn.execute("""
        INSERT OR REPLACE INTO listings_current
        (property_uid, source, source_listing_id, url, title, description_raw,
         price_mxn, area_m2, bedrooms, bathrooms, parking, floor, has_elevator,
         year_built, colonia, alcaldia, address_raw, lat, lon,
         is_new_development, is_recovery, recovery_flags,
         first_seen, last_seen, run_date, rent_hat, rent_hat_lo, rent_hat_hi,
         rent_source, listing_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        listing.property_uid, listing.source, listing.source_listing_id,
        listing.url, listing.title, listing.description_raw,
        listing.price_mxn, listing.area_m2, listing.bedrooms, listing.bathrooms,
        listing.parking, listing.floor,
        1 if listing.has_elevator else 0,
        listing.year_built, listing.colonia, listing.alcaldia, listing.address_raw,
        listing.lat, listing.lon,
        1 if listing.is_new_development else 0,
        1 if listing.is_recovery else 0,
        json.dumps(listing.recovery_flags) if listing.recovery_flags else "[]",
        first_seen, str(listing.last_seen), str(listing.run_date),
        listing.rent_hat, listing.rent_hat_lo, listing.rent_hat_hi,
        listing.rent_source, listing.listing_type,
    ))


def upsert_property(conn: sqlite3.Connection, listing):
    existing = conn.execute(
        "SELECT first_seen FROM properties WHERE property_uid = ?",
        (listing.property_uid,)
    ).fetchone()

    first_seen = existing["first_seen"] if existing else str(listing.first_seen)
    dom = (listing.last_seen - date.fromisoformat(first_seen)).days if listing.last_seen else 0

    source_count = conn.execute(
        "SELECT COUNT(DISTINCT source) FROM listings_current WHERE property_uid = ?",
        (listing.property_uid,)
    ).fetchone()[0] or 1

    conn.execute("""
        INSERT OR REPLACE INTO properties
        (property_uid, canonical_address, colonia, area_m2, bedrooms,
         first_seen, last_seen, status, days_on_market, source_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
    """, (
        listing.property_uid, listing.address_raw, listing.colonia,
        listing.area_m2, listing.bedrooms,
        first_seen, str(listing.last_seen), dom, source_count,
    ))


def detect_changes(conn: sqlite3.Connection, listings: list, run_date: date, config: dict) -> dict:
    sold_threshold = config.get("sold_threshold_days", 14)
    relist_gap = config.get("relist_gap_days", 7)

    current_uids = {l.property_uid for l in listings}
    changes = {"new": [], "price_drop": [], "price_rise": [], "back_on_market": [], "gone": [], "presumed_sold": []}

    for listing in listings:
        prev = conn.execute(
            "SELECT price_mxn, last_seen, first_seen FROM listings_current WHERE property_uid = ? ORDER BY last_seen DESC LIMIT 1",
            (listing.property_uid,)
        ).fetchone()

        if prev is None:
            old_prop = conn.execute(
                "SELECT last_seen FROM properties WHERE property_uid = ?",
                (listing.property_uid,)
            ).fetchone()
            if old_prop:
                last = date.fromisoformat(old_prop["last_seen"])
                gap = (run_date - last).days
                if gap >= relist_gap:
                    changes["back_on_market"].append(listing)
                    conn.execute(
                        "INSERT INTO listings_history (property_uid, source, source_listing_id, price_mxn, run_date, event) VALUES (?, ?, ?, ?, ?, ?)",
                        (listing.property_uid, listing.source, listing.source_listing_id, listing.price_mxn, str(run_date), "back_on_market")
                    )
                    continue
            changes["new"].append(listing)
            conn.execute(
                "INSERT INTO listings_history (property_uid, source, source_listing_id, price_mxn, run_date, event) VALUES (?, ?, ?, ?, ?, ?)",
                (listing.property_uid, listing.source, listing.source_listing_id, listing.price_mxn, str(run_date), "new")
            )
        else:
            old_price = prev["price_mxn"]
            if old_price and listing.price_mxn and old_price != listing.price_mxn:
                change_mxn = listing.price_mxn - old_price
                change_bp = int((change_mxn / old_price) * 10000) if old_price else 0
                event = "price_drop" if change_mxn < 0 else "price_rise"
                changes[event].append((listing, change_mxn, change_bp))
                conn.execute(
                    "INSERT INTO listings_history (property_uid, source, source_listing_id, price_mxn, run_date, event, price_change_mxn, price_change_bp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (listing.property_uid, listing.source, listing.source_listing_id, listing.price_mxn, str(run_date), event, change_mxn, change_bp)
                )

    prev_uids_rows = conn.execute(
        "SELECT DISTINCT property_uid FROM listings_current WHERE listing_type = 'sale'"
    ).fetchall()

    for row in prev_uids_rows:
        uid = row["property_uid"]
        if uid not in current_uids:
            prop = conn.execute(
                "SELECT last_seen, first_seen FROM properties WHERE property_uid = ?",
                (uid,)
            ).fetchone()
            if prop:
                last = date.fromisoformat(prop["last_seen"])
                gap = (run_date - last).days
                if gap >= sold_threshold:
                    first = date.fromisoformat(prop["first_seen"])
                    dom = (last - first).days
                    conn.execute(
                        "UPDATE properties SET status = 'presumed_sold', days_on_market = ? WHERE property_uid = ?",
                        (dom, uid)
                    )
                    changes["presumed_sold"].append(uid)
                else:
                    changes["gone"].append(uid)

    return changes


def save_timeseries(conn: sqlite3.Connection, aggregates: list, run_date: date):
    conn.execute("DELETE FROM market_timeseries WHERE run_date = ?", (str(run_date),))
    for agg in aggregates:
        conn.execute("""
            INSERT INTO market_timeseries
            (run_date, colonia, n_active, median_price_per_m2, p25_price_per_m2, p75_price_per_m2,
             median_gross_yield, median_days_on_market, new_count, presumed_sold_count, median_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(run_date), agg["colonia"], agg["n_active"],
            agg["median_price_per_m2"], agg["p25_price_per_m2"], agg["p75_price_per_m2"],
            agg["median_gross_yield"], agg.get("median_days_on_market", 0),
            agg.get("new_count", 0), agg.get("presumed_sold_count", 0),
            agg.get("median_price", 0),
        ))


def atomic_swap_db():
    if Path(DB_TEMP).exists():
        shutil.move(DB_TEMP, DB_PATH)
        logger.info(f"Atomic swap: {DB_TEMP} → {DB_PATH}")


def export_timeseries_json(conn: sqlite3.Connection, output_path: str = "data/market_timeseries.json"):
    rows = conn.execute(
        "SELECT * FROM market_timeseries ORDER BY run_date, colonia"
    ).fetchall()

    data = []
    for row in rows:
        data.append(dict(row))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Exported timeseries to {output_path}")
