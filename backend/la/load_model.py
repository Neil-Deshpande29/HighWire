"""Weather-driven hourly demand model (fit in validation/calibrate.py, used by V3 and the live forecast)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D

TZ = "America/Los_Angeles"


def local_hour_features(idx_utc: pd.DatetimeIndex):
    loc = idx_utc.tz_convert(TZ)
    return loc.hour.values, (loc.dayofweek.values >= 5).astype(int), loc


def pop_weighted_temp(wx, hours_idx=None):
    """Population-weighted 2 m temperature (nearest grid cell per Census tract)."""
    pop = D.population()
    lats, lons = wx["lats"], wx["lons"]
    j = np.clip(np.round((pop.lat.values - lats[0]) / (lats[1] - lats[0])).astype(int), 0, len(lats) - 1)
    i = np.clip(np.round((pop.lon.values - lons[0]) / (lons[1] - lons[0])).astype(int), 0, len(lons) - 1)
    w = np.zeros((len(lats), len(lons)))
    np.add.at(w, (j, i), pop["pop"].values)
    w /= w.sum()
    T = wx["temperature_2m"] if hours_idx is None else wx["temperature_2m"][hours_idx]
    return (T * w[None]).sum((1, 2))


def inland_temp(wx, hours_idx=None):
    T = wx["temperature_2m"] if hours_idx is None else wx["temperature_2m"][hours_idx]
    return np.percentile(T.reshape(len(T), -1), 90, axis=1)


def load_features(idx_utc, T, T24, Tin):
    """Hour-of-day x weekday/weekend profile + temperature terms.
    v2 (chosen on an internal 2019-20 fit / summer-2021 holdout, never on H2): air-conditioning load depends on heat
    MORE in the afternoon than at night, so temperature hinges are interacted with time-of-day blocks, and an inland
    heat term (90th-percentile grid temperature) is added because SCE load sits inland where the population-weighted
    mean understates the heat. Internal holdout: SCE top-5 peak error -13.1% -> -4.8%, LDWP -9.1% -> -5.9%."""
    hod, wkend, _ = local_hour_features(idx_utc)
    cols = [np.ones(len(T)), np.maximum(T - 18, 0), np.maximum(T - 26, 0), np.maximum(14 - T, 0),
            np.maximum(T24 - 20, 0), np.maximum(T24 - 26, 0)]
    for h in range(1, 24):
        cols.append((hod == h).astype(float))
    for h in range(24):
        cols.append(((hod == h) & (wkend == 1)).astype(float))
    for a, b in [(0, 6), (6, 11), (11, 15), (15, 22), (22, 24)]:
        k = ((hod >= a) & (hod < b)).astype(float)
        cols += [k * np.maximum(T - 22, 0), k * np.maximum(T24 - 22, 0)]
    aft = ((hod >= 12) & (hod < 22)).astype(float)
    cols += [aft * np.maximum(Tin - 28, 0), aft * np.maximum(Tin - 34, 0), np.maximum(Tin - 28, 0)]
    return np.column_stack(cols)


def predict_from_weather(wx, model=None):
    """Demand (LDWP, SCE) for every hour of a weather grid dict, e.g. the live forecast."""
    model = model or D.calibration()["load"]
    idx = pd.DatetimeIndex(wx["time"].astype("datetime64[ns]")).tz_localize("UTC")
    T = pd.Series(pop_weighted_temp(wx), idx)
    X = load_features(idx, T.values, T.rolling(24, min_periods=1).mean().values, inland_temp(wx))
    return {ba: X @ np.array(m["coef"]) for ba, m in model.items()}
