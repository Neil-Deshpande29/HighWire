"""Storm-damage model (the wind agent's physics). Answers "will wind and rain knock lines down and put customers out?",
which is a different question from the fire index ("will a line start a fire?").

Per tower span, each hour:
    p_damage = sigmoid((gust - onset) / 2)                      wind load on conductor + nearby trees
             * (0.5 + 0.5 * sigmoid((c50 - clearance) / 1))    wind-blown conductor close to vegetation fails more
Network hazard  H(t) = sum(p_damage * span_km) / total_span_km          (fraction of the network under damaging wind)
Rain hazard     Rn(t) = sum(sigmoid((precip_mm_h - 1) / 1) * span_km) / total_span_km   (wet storms: falling limbs, faults)
Customers stay out until crews restore them, so outages follow a DAMAGE STOCK, not the current weather:
    S(t) = S(t-1) * exp(-1/tau) + H(t)          (tau = restoration time constant, hours)
Storm index  P(t) = logistic(w0 + w1 * S_wind(t) + w2 * S_rain(t))  ~ chance this is a top-1% LA outage hour.

onset, tau, rain tau and w are fit ONLY on 2019-2021 (validation/calibrate.py fit_storm); scored on 2022-2024 (V4b)
and on the held-out events (V4a). Sag uses air temperature + 10 C (no power flow), identical in calibration and replay.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from . import data as D
from . import engine as E
from .. import thermal as TH

ONSET_GRID = (10, 12, 14, 16, 18)  # m/s, candidate damage-onset gusts searched in calibration
TAU_GRID = (6, 12, 24, 48)  # h, candidate restoration time constants
RAIN_TAU_GRID = (6, 12, 24)


def _clearance(grid, W, c_temp_offset=10.0):
    d = np.abs((W["wdir"] - grid.taz[None]) % 180.0)
    phi = np.radians(np.minimum(d, 180.0 - d))
    sag = TH.sag(W["temp"] + c_temp_offset, grid.tspan_m[None], grid.tsagref[None])
    theta = TH.blowout_angle(W["gust"] * np.sin(phi), grid.tcond["D"][None], grid.tcond["w_N_m"][None])
    return TH.clearance(sag, theta, grid.tattach[None], grid.trow[None], grid.tcanopy[None])


FAULT_C50 = 5.0  # m clearance half-point, inherited from the v1 fire calibration; not refit

tower_damage = E.damage_prob


def hazards(grid, wx, hours_idx, precip, onsets=None, cal=None, want_lines=False):
    """Hourly network hazards for the given hours. precip: (T, nlat, nlon) mm/h on wx's grid (or None).
    Returns dict: H[onset] (n,), Rn (n,), and if want_lines: line_damage (n, nl) for cal's onset."""
    cal = cal or D.calibration()
    c50 = FAULT_C50
    onsets = onsets or [cal["storm"]["onset"]]
    hours_idx = np.atleast_1d(hours_idx)
    span = grid.tspan_km.astype(np.float32)
    tot = float(span.sum())
    W = E.tower_weather(grid, wx, hours_idx, cal)
    clr = _clearance(grid, W)
    out = {"H": {}}
    for o in onsets:
        p = tower_damage(W["gust"], clr, o, c50).astype(np.float32)
        p = np.where(grid.cable[grid.tb][None], 0.0, p)  # underground cable: no wind exposure
        out["H"][o] = (p * span[None]).sum(1) / tot
        if want_lines:
            ld = np.zeros((len(hours_idx), grid.nl), np.float32)
            np.maximum.at(ld, (slice(None), grid.tb), p)
            out["line_damage"] = ld
    if precip is not None:
        w = E.interp_weights(grid.tlat, grid.tlon, wx["lats"], wx["lons"])
        pr = E.bilinear(np.nan_to_num(precip[hours_idx]), w)
        out["Rn"] = (E.sigmoid((pr - 1.0) / 1.0) * span[None]).sum(1) / tot
    else:
        out["Rn"] = np.zeros(len(hours_idx))
    return out


def stock(h, tau):
    return lfilter([1.0], [1.0, -np.exp(-1.0 / tau)], np.nan_to_num(np.asarray(h, dtype=float)))


def score(S_wind, S_rain, w):
    return E.sigmoid(w[0] + w[1] * S_wind + w[2] * S_rain)


def index_from_hazards(H, Rn, st):
    """Storm index (0-1) from hourly hazards using calibrated params st = calibration['storm']."""
    return score(stock(H, st["tau"]), stock(Rn, st["rain_tau"]), st["w"])


def event_storm(grid, eid, cal=None, wx=None, ev=None):
    """Storm index + per-line damage probability over an event's core hours. The damage stock is spun up over the
    pre-event weather pad so the first replay hour is not artificially calm."""
    cal = cal or D.calibration()
    st = cal["storm"]
    ev = ev or D.events()[eid]
    wx = wx if wx is not None else D.weather(eid)
    core = E.hours_for(wx, ev["start_utc"], ev["end_utc"])
    allh = np.arange(0, int(core[-1]) + 1)
    hz = hazards(grid, wx, allh, wx.get("precipitation"), cal=cal, want_lines=True)
    P = index_from_hazards(hz["H"][st["onset"]], hz["Rn"], st)
    return {"storm_index": P[core], "storm_hazard": hz["H"][st["onset"]][core], "rain_hazard": hz["Rn"][core],
            "line_damage": hz["line_damage"][core], "hours_idx": core}


def logfit(X, y, l2=1e-3, iters=60):
    """Plain L2-regularised logistic regression (Newton/IRLS). Returns [w0, w1, ...]."""
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-X @ w))
        Wt = p * (1 - p) + 1e-9
        H = X.T @ (X * Wt[:, None]) + l2 * np.eye(len(w))
        w = w + np.linalg.solve(H, X.T @ (y - p) - l2 * w)
    return w
