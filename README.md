# Highwire

_Fall 2026 HackPrinceton submission._

During storms, grid operators decide which power lines to reroute or shut off, while residents and 911 report downed
wires one call at a time. Highwire is a team of AI agents that reads what the weather is physically doing to the wires,
then hands the operator a short, ranked list of plans. **A deterministic physics engine tests every plan before the
operator sees it**, and the AI never switches anything on its own.

Phase 2 replaces the toy region with the **real Greater Los Angeles grid** built from public data, and **replays past
events hour by hour against what actually happened**: outages (EAGLE-I), utility ignitions (CPUC/WFIGS), a Public
Safety Power Shutoff (CPUC), NWS Red Flag Warnings and demand (EIA-930).

- **Results:** [`validation/REPORT.md`](validation/REPORT.md) (generated, with plots, baselines and limitations). The same
  numbers appear in the app's **Validation** tab. Every number is read from `validation/results/*.json`.
- **Choices and guesses:** [`validation/DECISIONS.md`](validation/DECISIONS.md), [`data/processed/grid_report.md`](data/processed/grid_report.md).
- **Modeled, not utility data:** line ratings, impedances, conductors, tower heights and load allocation are estimated
  from voltage class and Census population. The UI and report say so.

## Quick start (offline demo)

`data/processed/` and `validation/results/` are already built, so the app runs without network access or keys.

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
./run.sh          # backend :8000, frontend :5173
```

Open http://localhost:5173. There are three tabs:

1. **LA Replay.** Pick an event (E1 Woolsey 2018, E2 Saddleridge 2019, E3 Eaton/Hurst 2025, H1/H2 heat waves,
   C1-C3 calm weeks; each is tagged train or test). Scrub or play the hours. Lines are colored by fire risk (by
   loading for heat waves). The map also shows wind arrows, active Red Flag zones and fire origins at their real
   ignition times. The chart under the map is **model vs reality**: our risk index against actual LA County customers
   out, with the lead time marked. **Run Co-Pilot at this hour** runs the agent team on the real grid. Plans can
   include PSPS-style group shutoffs, and every plan is physics-checked before approval.
2. **Validation.** One card per check (V1-V7) with the headline number, its baseline, the plot, and a plain sentence
   on what it does and doesn't prove.
3. **What-if (synthetic).** The Phase-1 demo region (scenario presets, breaker clicks, reports, co-pilot).

## Rebuild everything from scratch

```bash
cp .env.example .env           # all keys optional (EIA bulk CSVs and Census PL files need no key)
make data                      # fetch 13 public sources -> data/raw (cache) -> data/processed
make grid                      # OSM/HIFLD -> LA grid model + sanity / plan-to-peak step
make calibrate                 # gust correction, load model, fire thresholds: TRAINING events only
make validate                  # V1-V7, replays, validation/REPORT.md
make test                      # pytest (grid sanity, IEEE 738 vs linerate, CIGRE TB 601 example, Phase-1 tests)
```

The train/test split is fixed in `validation/config.yaml` before anything runs: train E2, H1, C1, C2; test E1, E3, H2, C3.

## How it works

```
config/region_la.yaml          bbox, stations, counties, BAs (add region_houston.yaml later)
data/fetch/*.py                one idempotent fetcher per source (terrain, OSM, HIFLD archive, Open-Meteo, ASOS, EIA-930,
                               EAGLE-I, WFIGS, Red Flags, EIA-860, Census PL 94-171)
data/build_grid.py             OSM lines >= 66 kV -> buses/branches, conductor params by voltage class, tower points
                               every span (elevation, zone, slope, canopy), Voronoi population loads, EIA-860 plants, imports
data/sanity_grid.py            islands sourced, DC power flow at median load, plan-to-peak rating step (logged)
backend/thermal.py             our vectorized IEEE 738 (checked against Statnett linerate), sag, blowout, clearance
backend/la/engine.py           hourly hindcast: PTDF power flow -> current -> conductor temp -> sag -> blowout ->
                               vegetation clearance -> fire risk per tower point; region risk index
backend/la/state.py            the real grid at one replay hour, same interface as the Phase-1 planner/tools
backend/la/replay.py           serves precomputed replays (validation/results/replay_*.npz) to the UI
validation/calibrate.py        fits only the unknowns, only on training data -> data/processed/calibration.json
validation/checks/v1..v7       thermal, weather, demand, outage replay, multi-year discrimination, ignitions, PSPS, Red Flags
validation/run_all.py          runs the checks, builds replays, writes REPORT.md + results/summary.json
```

New API routes: `GET /events`, `GET /replay/{event}?t=ISO_HOUR` (or `?i=`), `GET /la/series/{event}`, `GET /validation`,
`GET /report`, `GET /results/*.png`, `POST /la/copilot/run`, `POST /la/plans/{id}/approve|reject`. The Phase-1 routes are unchanged.

The agents get two new tools, `get_validation_summary()` and `get_event_context(event_id, t)`. Both read saved results
files, so the agents can cite the validation but never compute numbers. Without `ANTHROPIC_API_KEY` the rule-based
planner runs. That planner was also extended for the LA grid: fire-risk violations, and PSPS groups split by zone and
voltage tier.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the deepagents LLM team; otherwise the rule-based planner runs. |
| `MODEL` | `claude-sonnet-5-5` | Anthropic model id. |
| `AGENT_TIMEOUT_S` | `60` | Fall back to the rule-based planner after this many seconds or on any error. |
| `EIA_API_KEY`, `CENSUS_API_KEY` | unset | Optional. The fetchers use keyless bulk files. |
| `MOCK_PACE` | `0.35` | Seconds between rule-based log lines so the activity log visibly streams. |
