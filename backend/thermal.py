"""Conductor thermal model: our own vectorized IEEE 738 steady-state implementation (numpy), plus sag,
wind blowout and vegetation clearance. Validated against Statnett's `linerate` in validation/checks/v1_thermal.py.

Equations (IEEE Std 738, SI form):
  air at film temp Tf=(Tc+Ta)/2:  mu = 1.458e-6 (Tf+273)^1.5 / (Tf+383.4)
                                  rho = (1.293 - 1.525e-4 He + 6.379e-9 He^2) / (1 + 0.00367 Tf)
                                  k   = 2.424e-2 + 7.477e-5 Tf - 4.407e-9 Tf^2
  Kangle = 1.194 - cos(phi) + 0.194 cos(2 phi) + 0.368 sin(2 phi)          (phi = wind vs conductor axis)
  NRe = D rho V / mu
  qc1 = Kangle (1.01 + 1.35 NRe^0.52) k (Tc-Ta);  qc2 = Kangle 0.754 NRe^0.6 k (Tc-Ta)
  qcn = 3.645 rho^0.5 D^0.75 (Tc-Ta)^1.25;        qc = max(qc1, qc2, qcn)
  qr  = pi D sigma eps [(Tc+273.15)^4 - (Ta+273.15)^4]   (IEEE's 17.8 D eps form with the exact constant)
  qs  = alpha * Q * D          (Q = measured/forecast global horizontal irradiance, W/m^2)
  qj  = I^2 R(Tc),  R linear between (T1,R1) and (T2,R2)
  Steady state: qj + qs = qc + qr, solved for Tc by vectorized bisection.
"""
from __future__ import annotations

import numpy as np

# Typical ACSR conductors per voltage class (MODELED, see data/build_grid.py). Resistances are AC at 25/75 C.
CONDUCTORS = {
    "Linnet": {"D": 0.01829, "R1": 1.738e-4, "R2": 2.076e-4, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "w_N_m": 6.75, "n": 1, "tmax": 75.0},
    "Hawk": {"D": 0.02179, "R1": 1.196e-4, "R2": 1.430e-4, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "w_N_m": 9.56, "n": 1, "tmax": 75.0},
    "Drake": {"D": 0.02814, "R1": 7.284e-5, "R2": 8.689e-5, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "w_N_m": 15.97, "n": 1, "tmax": 100.0},
    "Drake3": {"D": 0.02814, "R1": 7.284e-5, "R2": 8.689e-5, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "w_N_m": 15.97, "n": 3, "tmax": 100.0},
}
ALPHA_L = 2.3e-5  # aluminium thermal expansion 1/C


def resistance(c, T):
    return c["R1"] + (c["R2"] - c["R1"]) / (c["T2"] - c["T1"]) * (T - c["T1"])


def k_angle(phi_rad):
    return 1.194 - np.cos(phi_rad) + 0.194 * np.cos(2 * phi_rad) + 0.368 * np.sin(2 * phi_rad)


def q_convection(Tc, Ta, V, phi_rad, D, He):
    Tf = (Tc + Ta) / 2.0
    mu = 1.458e-6 * (Tf + 273.0) ** 1.5 / (Tf + 383.4)
    rho = (1.293 - 1.525e-4 * He + 6.379e-9 * He**2) / (1.0 + 0.00367 * Tf)
    k = 2.424e-2 + 7.477e-5 * Tf - 4.407e-9 * Tf**2
    dT = np.maximum(Tc - Ta, 0.0)
    nre = D * rho * np.maximum(V, 0.0) / mu
    ka = k_angle(phi_rad)
    qc1 = ka * (1.01 + 1.35 * nre**0.52) * k * dT
    qc2 = ka * 0.754 * nre**0.6 * k * dT
    qcn = 3.645 * np.sqrt(rho) * D**0.75 * dT**1.25
    return np.maximum(np.maximum(qc1, qc2), qcn)


SIGMA = 5.6703744191844314e-08


def q_radiation(Tc, Ta, D, eps):
    return np.pi * D * SIGMA * eps * ((Tc + 273.15) ** 4 - (Ta + 273.15) ** 4)


def conductor_temperature(c, current_a, Ta, V, phi_rad, ghi, He, q_solar=None, iters=40):
    """Steady-state conductor temperature (C). All inputs broadcast. current_a is per PHASE; bundles split it."""
    I = np.asarray(current_a, dtype=float) / c["n"]
    Ta = np.asarray(Ta, dtype=float)
    qs = c["alpha"] * np.asarray(ghi, dtype=float) * c["D"] if q_solar is None else np.asarray(q_solar, dtype=float)
    lo = np.broadcast_to(Ta, np.broadcast(I, Ta, V, phi_rad, qs, He).shape).astype(float).copy()
    hi = lo + 250.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        bal = I**2 * resistance(c, mid) + qs - q_convection(mid, Ta, V, phi_rad, c["D"], He) - q_radiation(mid, Ta, c["D"], c["eps"])
        up = bal > 0
        lo = np.where(up, mid, lo)
        hi = np.where(up, hi, mid)
    return (lo + hi) / 2


def ampacity(c, Tmax, Ta, V, phi_rad, ghi, He, q_solar=None):
    """Steady-state ampacity (A per phase, all subconductors) at conductor temperature Tmax."""
    qs = c["alpha"] * np.asarray(ghi, dtype=float) * c["D"] if q_solar is None else np.asarray(q_solar, dtype=float)
    num = q_convection(Tmax, Ta, V, phi_rad, c["D"], He) + q_radiation(Tmax, Ta, c["D"], c["eps"]) - qs
    return np.sqrt(np.maximum(num, 0.0) / resistance(c, Tmax)) * c["n"]


def static_rating(c) -> float:
    """Standard static rating assumptions: 40 C ambient, 0.61 m/s perpendicular wind, 1000 W/m2 sun, sea level."""
    return float(ampacity(c, c["tmax"], 40.0, 0.61, np.pi / 2, 1000.0, 0.0))


# ---------------------------------------------------------------- sag, blowout, clearance
def sag(T, span_m, sag_ref_m, T_ref=25.0):
    """Parabolic sag from thermal elongation (inextensible approximation)."""
    l_ref = span_m + 8.0 * sag_ref_m**2 / (3.0 * span_m)
    l_t = l_ref * (1.0 + ALPHA_L * (np.asarray(T) - T_ref))
    return np.sqrt(np.maximum(3.0 * span_m * (l_t - span_m) / 8.0, 0.0))


def blowout_angle(gust_perp_ms, D, w_N_m):
    """Swing angle from wind pressure 0.6 V^2 (N/m^2) on the projected diameter vs conductor weight."""
    return np.arctan(0.6 * np.asarray(gust_perp_ms) ** 2 * D / w_N_m)


def clearance(sag_m, theta, attach_m, row_half_m, canopy_m):
    """Distance from the conductor low point to the nearest vegetation top at the ROW edge (cross-section).
    Wind swings the conductor sideways by sag*sin(theta) toward the downwind ROW edge."""
    z = attach_m - sag_m * np.cos(theta)
    dx = np.maximum(row_half_m - sag_m * np.sin(theta), 0.0)
    dz = np.maximum(z - canopy_m, 0.0)
    return np.hypot(dx, dz)
