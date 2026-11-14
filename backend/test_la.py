"""Phase-2 tests: LA grid sanity, thermal model vs linerate, hindcast performance."""
import time
import warnings

import numpy as np
import pytest

from backend import thermal as TH
from backend.la import data as D
from backend.la import engine as E

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def grid():
    return E.LAGrid()


# ---------------------------------------------------------------- grid sanity
def test_every_island_has_a_source(grid):
    top = grid.base_topology()
    _, sourced, _, roots = grid.ptdf(top)
    assert sourced.all(), "a bus sits in an island with no generator/import"


def test_dc_power_flow_solves_at_median_load(grid):
    dem = D.demand()
    top = grid.base_topology()
    P, load = grid.injections(dem["LDWP"].median(), dem["SCE"].median(), top)
    F, _ = grid.flows(P, top)
    assert np.isfinite(F).all()
    # flows balance: injections sum to ~0 in every island (slack absorbs the residual)
    assert abs(P.sum()) < 1e6


def test_no_line_over_150pct_at_median_load(grid):
    dem = D.demand()
    top = grid.base_topology()
    P, _ = grid.injections(dem["LDWP"].median(), dem["SCE"].median(), top)
    F, _ = grid.flows(P, top)
    amps = np.abs(F[0]) * 1e6 / (np.sqrt(3) * grid.kv * 1e3)
    assert (amps / grid.rating * 100).max() < 150


def test_grid_is_geographically_real(grid):
    b = D.grid()["bbox"]
    assert len(grid.branches) >= 60 and len(grid.buses) >= 60
    # Overpass returns whole ways that cross the bbox, so a few far ends (e.g. 500 kV to Vincent) sit outside it
    inside = [b["lat_min"] <= x["lat"] <= b["lat_max"] and b["lon_min"] <= x["lon"] <= b["lon_max"] for x in grid.buses]
    assert np.mean(inside) > 0.9


# ---------------------------------------------------------------- thermal vs linerate
def test_linerate_reproduces_cigre601_example_b():
    import linerate
    from linerate.models.cigre601 import Cigre601

    c = linerate.Conductor(core_diameter=10.4e-3, conductor_diameter=28.1e-3, outer_layer_strand_diameter=2.2e-3, emissivity=0.9,
                           solar_absorptivity=0.9, temperature1=25, temperature2=75, resistance_at_temperature1=7.283e-5,
                           resistance_at_temperature2=8.688e-5, aluminium_cross_section_area=float("nan"), constant_magnetic_effect=1,
                           current_density_proportional_magnetic_effect=0, max_magnetic_core_relative_resistance_increase=1)
    span = linerate.Span(conductor=c, start_tower=linerate.Tower(latitude=50 - 0.0045, longitude=0, altitude=412),
                         end_tower=linerate.Tower(latitude=50 + 0.0045, longitude=0, altitude=588), num_conductors=1)
    w = linerate.Weather(air_temperature=20, wind_direction=np.radians(80), wind_speed=1.66, ground_albedo=0.15, clearness_ratio=0.5)
    m = Cigre601(span, w, np.datetime64("2016-10-03 14:00"))
    amp = m.compute_steady_state_ampacity(100, tolerance=1e-8)
    assert abs(amp - 1504) / 1504 < 0.01
    # temperature at that current comes back to 100 C within 1 C
    assert abs(m.compute_conductor_temperature(amp, tolerance=1e-4) - 100) < 1.0


def test_ieee738_matches_linerate_sweep():
    import linerate
    from linerate.models.ieee738 import IEEE738

    dr = linerate.Conductor(conductor_diameter=0.02812, emissivity=0.5, solar_absorptivity=0.5, temperature1=25, temperature2=75,
                            resistance_at_temperature1=7.284e-5, resistance_at_temperature2=8.689e-5, core_diameter=10.4e-3,
                            outer_layer_strand_diameter=4.4e-3, aluminium_cross_section_area=float("nan"), constant_magnetic_effect=1,
                            current_density_proportional_magnetic_effect=0, max_magnetic_core_relative_resistance_increase=1)
    co = {"D": 0.02812, "R1": 7.284e-5, "R2": 8.689e-5, "T1": 25.0, "T2": 75.0, "eps": 0.5, "alpha": 0.5, "n": 1}
    worst = 0.0
    for I in (150, 600, 1100):
        for V in (0.3, 2.0, 8.0):
            for Ta in (0, 25, 42):
                for alt in (0, 1500):
                    t = linerate.Tower(latitude=34, longitude=-118, altitude=alt)
                    sp = linerate.Span(conductor=dr, start_tower=t, end_tower=t, num_conductors=1)
                    lm = IEEE738(sp, linerate.Weather(air_temperature=Ta, wind_direction=np.radians(90), wind_speed=V,
                                                      ground_albedo=0.15, clearness_ratio=1), np.datetime64("2020-01-15 10:00"))
                    tl = lm.compute_conductor_temperature(I, min_temperature=Ta, max_temperature=Ta + 400, tolerance=1e-4)
                    to = TH.conductor_temperature(co, I, Ta, V, np.pi / 2, 0.0, alt, iters=50)
                    worst = max(worst, abs(float(to) - float(tl)))
    assert worst < 2.0


def test_thin_air_runs_hotter():
    c = TH.CONDUCTORS["Drake"]
    assert TH.conductor_temperature(c, 800, 25, 1.0, np.pi / 2, 500, 1500) > TH.conductor_temperature(c, 800, 25, 1.0, np.pi / 2, 500, 0)


# ---------------------------------------------------------------- performance
def test_five_day_hindcast_under_two_minutes(grid):
    ev = D.events()["E1"]
    wx = D.weather("E1")
    hrs = E.hours_for(wx, ev["start_utc"], ev["end_utc"])
    ld, sc = E.event_demand(wx["time"][hrs])
    t = time.time()
    r = E.hindcast(grid, wx, hrs, ld, sc)
    assert time.time() - t < 120
    assert r["line_risk"].shape == (len(hrs), grid.nl) and len(hrs) == 120
