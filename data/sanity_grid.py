"""Grid sanity step (run after build_grid.py): drop islands without a source, solve DC power flow at the median
and at the 2019-2021 (training years) peak hour (EIA-930), and "plan to peak": any branch above 90% at peak load gets more
modeled conductor (thermal_scale = more/larger conductors) so it sits at 90% at peak load. Every change is logged
into grid_report.md. Writes grid_<region>.json in place."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.la import data as D  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402


def main():
    g = D.grid()
    grid = LAGrid(g)
    top = grid.base_topology()
    dem = D.demand()
    ld, sc = float(dem["LDWP"].median()), float(dem["SCE"].median())
    fit = dem[dem.index.year.isin(D.config()["load_model"]["fit_years"])]  # training years only (no peeking at H2)
    tot = fit["LDWP"] + fit["SCE"] * grid.ba_scale["SCE"]
    tpk = tot.idxmax()
    ld99, sc99 = float(fit.loc[tpk, "LDWP"]), float(fit.loc[tpk, "SCE"])
    P, load = grid.injections(ld, sc, top)
    F, sourced = grid.flows(P, top)
    amps = np.abs(F[0]) * 1e6 / (np.sqrt(3) * grid.kv * 1e3)
    loading = amps / grid.rating * 100
    _, roots = grid.islands(top)
    n_isl = len(np.unique(roots))
    unsourced = int((~sourced).sum())
    if unsourced:  # drop islands with no generator/import (and their branches), then rebuild
        drop = {g["buses"][i]["id"] for i in np.where(~sourced)[0]}
        g["buses"] = [b for b in g["buses"] if b["id"] not in drop]
        g["branches"] = [l for l in g["branches"] if l["from_bus"] not in drop and l["to_bus"] not in drop]
        g["critical_sites"] = [c for c in g["critical_sites"] if c["bus"] not in drop]
        grid = LAGrid(g)
        top = grid.base_topology()
        P, load = grid.injections(ld, sc, top)
        F, sourced = grid.flows(P, top)
        amps = np.abs(F[0]) * 1e6 / (np.sqrt(3) * grid.kv * 1e3)
        loading = amps / grid.rating * 100
        _, roots = grid.islands(top)
    med_max_before = loading.max()
    P99, _ = grid.injections(ld99, sc99, top)
    F99, _ = grid.flows(P99, top)
    amps99 = np.abs(F99[0]) * 1e6 / (np.sqrt(3) * grid.kv * 1e3)
    load99 = amps99 / grid.rating * 100
    changed = []
    for i in np.where(load99 > 90)[0]:
        old = g["branches"][i]["rating_a"]
        new = round(float(amps99[i] / 0.9), 1)
        g["branches"][i]["rating_a"] = new
        g["branches"][i]["thermal_scale"] = round(new / old, 4)
        g["branches"][i]["rating_note"] = f"raised from {old} A (peak-load flow {load99[i]:.0f}%, median {loading[i]:.0f}%)"
        changed.append((g["branches"][i]["id"], old, new, load99[i]))
    with np.errstate(invalid="ignore", divide="ignore"):
        loading = np.where(load99 > 90, amps / (amps99 / 0.9) * 100, loading)
    D.P.joinpath(f"grid_{D.REGION}.json").write_text(json.dumps(g))
    rep = D.P / "grid_report.md"
    txt = rep.read_text().split("\n## Sanity checks")[0]
    txt += "\n## Sanity checks (data/sanity_grid.py)\n\n"
    txt += f"- Median hourly demand used: LDWP {ld:.0f} MW, SCE sub-BA {sc:.0f} MW -> modeled regional load {load.sum():.0f} MW\n"
    txt += f"- Islands: {n_isl}; {unsourced} buses in islands without any generator/import were dropped; every remaining island has a source: {bool(sourced.all())}\n"
    txt += f"- peak hourly demand: LDWP {ld99:.0f} MW, SCE sub-BA {sc99:.0f} MW\n"
    txt += f"- DC power flow solved; max loading at median load before adjustment {med_max_before:.0f}%, after {loading.max():.0f}% (test: < 150%)\n"
    txt += f"- Plan-to-peak: {len(changed)} branches above 90% at peak load got more modeled conductor (rating and thermal_scale raised; the simplified topology concentrates flow near import points):\n"
    for lid, old, new, pct in changed:
        txt += f"  - {lid}: {old} A -> {new} A (was {pct:.0f}%)\n"
    rep.write_text(txt)
    print(f"islands={n_isl} unsourced_buses={unsourced} raised={len(changed)} median_max_before={med_max_before:.0f}% after={loading.max():.0f}%")


if __name__ == "__main__":
    main()
