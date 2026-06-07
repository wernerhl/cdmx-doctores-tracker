# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MODELS_DIR = Path("models")


def _load_latest_model(run_date: date) -> Optional[dict]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    candidates = sorted(MODELS_DIR.glob("rent_hedonic_*.json"), reverse=True)
    for path in candidates:
        try:
            with open(path) as f:
                model = json.load(f)
            model_date = date.fromisoformat(model["fit_date"])
            if model_date <= run_date:
                return model
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return None


def _save_model(model: dict, run_date: date):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"rent_hedonic_{run_date}.json"
    with open(path, "w") as f:
        json.dump(model, f, indent=2)
    logger.info(f"Saved rent model to {path}")


def should_refit(run_date: date, config: dict) -> bool:
    refit_day = config.get("rent_model", {}).get("refit_day", 0)
    if run_date.weekday() != refit_day:
        existing = _load_latest_model(run_date)
        if existing:
            return False
    return True


def fit_hedonic(rentals: list, run_date: date, config: dict) -> dict:
    valid = [
        r for r in rentals
        if r.price_mxn and r.price_mxn > 0
        and r.area_m2 and r.area_m2 > 0
        and r.bedrooms is not None
    ]

    if len(valid) < 10:
        logger.warning(f"Only {len(valid)} valid rental comps — model will be weak")

    colonias = sorted(set(r.colonia for r in valid if r.colonia))
    colonia_map = {c: i for i, c in enumerate(colonias)}

    n = len(valid)
    n_colonia = len(colonias)
    n_features = 5 + max(n_colonia - 1, 0)

    X = np.zeros((n, n_features))
    y = np.zeros(n)

    for i, r in enumerate(valid):
        X[i, 0] = math.log(r.area_m2) if r.area_m2 > 0 else 0
        X[i, 1] = r.bedrooms
        X[i, 2] = r.bathrooms or 0
        X[i, 3] = r.parking or 0
        X[i, 4] = 1.0 if r.has_elevator else 0.0

        col_idx = colonia_map.get(r.colonia, -1)
        if col_idx > 0:
            X[i, 5 + col_idx - 1] = 1.0

        y[i] = math.log(r.price_mxn)

    X_bias = np.column_stack([np.ones(n), X])

    try:
        beta, residuals, rank, sv = np.linalg.lstsq(X_bias, y, rcond=None)
    except np.linalg.LinAlgError:
        logger.error("OLS fit failed")
        return _empty_model(run_date)

    y_hat = X_bias @ beta
    resid = y - y_hat
    sse = np.sum(resid ** 2)
    sst = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - sse / sst if sst > 0 else 0
    residual_se = math.sqrt(sse / max(n - n_features - 1, 1))

    pi_pct = config.get("rent_model", {}).get("prediction_interval", 0.80)
    z_val = 1.28 if pi_pct == 0.80 else 1.96

    feature_names = ["intercept", "log_area_m2", "bedrooms", "bathrooms", "parking", "has_elevator"]
    for c in colonias[1:]:
        feature_names.append(f"colonia_{c}")

    model = {
        "fit_date": str(run_date),
        "n": n,
        "r_squared": round(r_squared, 4),
        "residual_se": round(residual_se, 4),
        "z_val": z_val,
        "beta": [round(b, 6) for b in beta.tolist()],
        "feature_names": feature_names,
        "colonias": colonias,
        "colonia_map": colonia_map,
    }

    _save_model(model, run_date)
    logger.info(f"Hedonic model: n={n}, R²={r_squared:.3f}, SE={residual_se:.3f}")
    return model


def _empty_model(run_date: date) -> dict:
    return {
        "fit_date": str(run_date),
        "n": 0,
        "r_squared": 0.0,
        "residual_se": 1.0,
        "z_val": 1.28,
        "beta": [],
        "feature_names": [],
        "colonias": [],
        "colonia_map": {},
    }


def predict_rent(listing, model: dict, config: dict) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    min_comps = config.get("rent_model", {}).get("min_comps", 15)

    if not model or not model.get("beta") or model["n"] < min_comps:
        return _fallback_rent(listing, config)

    colonias = model.get("colonias", [])
    colonia_map = model.get("colonia_map", {})
    beta = np.array(model["beta"])
    n_colonia = len(colonias)
    n_features = 5 + max(n_colonia - 1, 0)

    x = np.zeros(n_features)
    x[0] = math.log(listing.area_m2) if listing.area_m2 and listing.area_m2 > 0 else math.log(50)
    x[1] = listing.bedrooms or 2
    x[2] = listing.bathrooms or 1
    x[3] = listing.parking or 0
    x[4] = 1.0 if listing.has_elevator else 0.0

    col_idx = colonia_map.get(listing.colonia, -1)
    if col_idx > 0 and (5 + col_idx - 1) < n_features:
        x[5 + col_idx - 1] = 1.0

    x_bias = np.concatenate([[1.0], x])
    if len(x_bias) != len(beta):
        return _fallback_rent(listing, config)

    log_rent = float(x_bias @ beta)
    se = model.get("residual_se", 0.5)
    z = model.get("z_val", 1.28)

    rent_hat = math.exp(log_rent)
    rent_lo = math.exp(log_rent - z * se)
    rent_hi = math.exp(log_rent + z * se)

    return rent_hat, rent_lo, rent_hi, "hedonic"


def _fallback_rent(listing, config: dict) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    tier1_rent_per_m2 = 180
    tier2_rent_per_m2 = 210

    colonia_rates = {
        "Doctores": 175, "Obrera": 165, "Algarín": 170,
        "Buenos Aires": 160, "Centro": 200, "Guerrero": 170,
        "Roma Sur": 250, "Santa María la Ribera": 190,
    }

    rate = colonia_rates.get(listing.colonia, tier1_rent_per_m2)
    area = listing.area_m2 if listing.area_m2 and listing.area_m2 > 0 else 50

    rent_hat = rate * area
    rent_lo = rent_hat * 0.75
    rent_hi = rent_hat * 1.25

    return rent_hat, rent_lo, rent_hi, "fallback"
