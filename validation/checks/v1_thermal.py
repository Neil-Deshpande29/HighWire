"""V1. Thermal model vs reference implementation (linerate, Statnett).
(a) linerate reproduces CIGRE TB 601 Annex E, Example B published values (reference sanity).
(b) our IEEE 738 convection reproduces IEEE 738 Annex B (83.06 W/m at 100.7 C).
(c) our IEEE 738 vs linerate IEEE738 across a sweep of current, wind, ambient, elevation, angle: max |dT| <= 2 C.
Proves the physics is implemented correctly; it does NOT prove it predicts real conductors."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import itertools
import warnings

import matplotlib
import numpy as np

matplotlib.use("Agg")
import linerate  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from linerate.models.cigre601 import Cigre601  # noqa: E402
from linerate.models.ieee738 import IEEE738  # noqa: E402

from common import RES, save  # noqa: E402
from backend import thermal as TH  # noqa: E402

warnings.filterwarnings("ignore")
TB601_B = {"ampacity_a": 1504, "convective_w_m": 172.1, "radiative_w_m": 54.0, "solar_w_m": 13.7}  # CIGRE TB 601 Annex E (B)
IEEE_B = {"convective_w_m_at_100_7C": 83.061, "solar_w_m": 13.738}  # IEEE 738 Annex B


def _drake(cigre=False):
    return linerate.Conductor(
        core_diameter=10.4e-3, conductor_diameter=0.0281 if cigre else 0.02812,
        outer_layer_strand_diameter=2.2e-3 if cigre else 4.4e-3, emissivity=0.9 if cigre else 0.5,
        solar_absorptivity=0.9 if cigre else 0.5, temperature1=25, temperature2=75,
        resistance_at_temperature1=7.283e-5 if cigre else 7.284e-5, resistance_at_temperature2=8.688e-5 if cigre else 8.689e-5,
        aluminium_cross_section_area=float("nan"), constant_magnetic_effect=1, current_density_proportional_magnetic_effect=0,
        max_magnetic_core_relative_resistance_increase=1)


def run():
    # (a) CIGRE TB 601 Example B through linerate
    cond = _drake(cigre=True)
    span = linerate.Span(conductor=cond, start_tower=linerate.Tower(latitude=50 - 0.0045, longitude=0, altitude=412),
                         end_tower=linerate.Tower(latitude=50 + 0.0045, longitude=0, altitude=588), num_conductors=1)
    w = linerate.Weather(air_temperature=20, wind_direction=np.radians(80), wind_speed=1.66, ground_albedo=0.15, clearness_ratio=0.5)
    m = Cigre601(span, w, np.datetime64("2016-10-03 14:00"))
    ref = {"ampacity_a": float(m.compute_steady_state_ampacity(100, tolerance=1e-8)),
           "convective_w_m": float(m.compute_convective_cooling(100)), "radiative_w_m": float(m.compute_radiative_cooling(100)),
           "solar_w_m": float(m.compute_solar_heating())}
    a_err = abs(ref["ampacity_a"] - TB601_B["ampacity_a"]) / TB601_B["ampacity_a"] * 100
    a_pass = a_err <= 1.0 and all(abs(ref[k] - TB601_B[k]) <= 1.0 for k in ("convective_w_m", "radiative_w_m", "solar_w_m"))
    # our IEEE 738 on the same Example B inputs (different standard: informational)
    c = {"D": 0.0281, "R1": 7.283e-5, "R2": 8.688e-5, "T1": 25.0, "T2": 75.0, "eps": 0.9, "alpha": 0.9, "n": 1}
    ours_b = float(TH.ampacity(c, 100.0, 20.0, 1.66, np.radians(80), 0.0, 500.0, q_solar=ref["solar_w_m"]))
    # (b) IEEE Annex B convection
    qc_ours = float(TH.q_convection(100.7, 40.0, 0.61, np.pi / 2, 0.02812, 0.0))
    b_pass = abs(qc_ours - IEEE_B["convective_w_m_at_100_7C"]) <= 0.5
    # (c) sweep vs linerate IEEE738 (night, so both use zero solar)
    dr = _drake()
    co = {"D": 0.02812, "R1": 7.284e-5, "R2": 8.689e-5, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "n": 1}
    rows = []
    for I, V, Ta, alt, ang in itertools.product([100, 400, 700, 1000, 1300], [0.2, 0.61, 2, 5, 10], [-5, 15, 30, 45], [0, 800, 1800], [30, 90]):
        t = linerate.Tower(latitude=34, longitude=-118, altitude=alt)
        sp = linerate.Span(conductor=dr, start_tower=t, end_tower=t, num_conductors=1)
        lm = IEEE738(sp, linerate.Weather(air_temperature=Ta, wind_direction=np.radians(ang), wind_speed=V, ground_albedo=0.15,
                                          clearness_ratio=1), np.datetime64("2020-01-15 10:00"))  # 02:00 local: night
        tl = float(lm.compute_conductor_temperature(I, min_temperature=Ta, max_temperature=Ta + 400, tolerance=1e-4))
        to = float(TH.conductor_temperature(co, I, Ta, V, np.radians(ang), 0.0, alt, iters=50))
        rows.append((I, V, Ta, alt, ang, tl, to))
    arr = np.array(rows)
    err = arr[:, 6] - arr[:, 5]
    c_pass = float(np.abs(err).max()) <= 2.0
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    ax[0].scatter(arr[:, 5], arr[:, 6], s=8)
    lim = [arr[:, 5:].min(), arr[:, 5:].max()]
    ax[0].plot(lim, lim, "k--", lw=0.8)
    ax[0].set(xlabel="linerate IEEE738 conductor temp (C)", ylabel="Rewire IEEE 738 (C)", title=f"{len(arr)}-case sweep")
    ax[1].hist(err, bins=30)
    ax[1].set(xlabel="Rewire - linerate (C)", title=f"max |error| {np.abs(err).max():.3f} C (tolerance 2 C)")
    fig.tight_layout()
    fig.savefig(RES / "v1_thermal.png", dpi=110)
    plt.close(fig)
    res = {"check": "V1", "pass": bool(a_pass and b_pass and c_pass),
           "a_cigre601_example_b": {"published": TB601_B, "linerate": ref, "ampacity_error_pct": a_err, "pass": a_pass,
                                    "rewire_ieee738_ampacity_same_inputs_a": ours_b,
                                    "note": "IEEE 738 and CIGRE 601 are different standards; our IEEE number on CIGRE inputs is informational."},
           "b_ieee738_annex_b": {"published_convective": IEEE_B["convective_w_m_at_100_7C"], "rewire": qc_ours, "pass": b_pass},
           "c_sweep": {"n_cases": len(arr), "max_abs_err_c": float(np.abs(err).max()), "mean_err_c": float(err.mean()),
                       "tolerance_c": 2.0, "pass": c_pass},
           "headline": f"Our IEEE 738 matches linerate within {np.abs(err).max():.2f} C over {len(arr)} cases",
           "baseline": "Reference implementation (linerate) and published CIGRE/IEEE worked examples",
           "plot": "v1_thermal.png"}
    save("v1_thermal", res)
    return res


if __name__ == "__main__":
    print(run())
