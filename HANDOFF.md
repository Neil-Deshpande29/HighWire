# Highwire handoff

Where the project stands, how to run it, what is proven, and what to do next. Last updated 2026-11-14.

## State

- **Branch:** `v2-storm-forecast` (3 commits ahead of `main`, not pushed). `main` is the last pushed version (Phase 2 + co-pilot safety policy).
- **Tests:** `make test`, 21 passing. Frontend: `cd frontend && npx tsc --noEmit` clean, `vite build` OK.
- **LLM:** Google Gemini via LangChain (`backend/llm.py`). **No API key is configured yet**, so every co-pilot run uses
  the rule-based planner. Add `GEMINI_API_KEY` to `.env` (free at https://aistudio.google.com/apikey) to turn on the AI team.
  The Gemini model id (`GEMINI_MODEL`, default `gemini-3.5-flash`) has not been tried against the live API yet.

## Run it

```bash
./run.sh                 # backend :8000, frontend :5173 -> open http://localhost:5173
make forecast            # pull today's forecast and rebuild the 🔮 Live event (or use the refresh button)
make calibrate validate  # refit (training data only) and rerun V1-V8 + REPORT.md (~15 min)
```

`data/processed/` and `validation/results/` are committed, so the app runs offline. The 🔮 Live event shows whatever
forecast was last fetched until you refresh it.

## What the models are (and which ones the app uses)

| Model | File | In the app? |
|---|---|---|
| DC power flow, IEEE 738 conductor temperature, sag, blowout, clearance | `backend/la/engine.py`, `backend/thermal.py` | yes |
| Fire risk (legacy blended score: gust x dryness x clearance x zone fuel) | `engine.py` `hindcast` | **yes** (`validation/config.yaml: fire_model: legacy`) |
| Fire risk, wind-pressure variant (gust^2 wind term, v4) | `engine.py` `wind_pressure` | opt-in (`fire_model: wind_pressure`); results in `validation/results/wind_pressure_v4/` |
| Gemini site agent (aerial photos along alarmed lines, cross-checked) | `site_agent/` | yes: Site agent panel, `get_site_context` for fire/wind/brownout agents + coordinator; context only |
| Storm damage (wind + rain damage stock, restoration time constant) | `backend/la/storm.py` | yes: map layer, storm index, crew staging |
| Demand (heat x time of day, inland heat) | `backend/la/load_model.py` | yes: V3, live forecast |
| Real vegetation heights (LANDFIRE EVH) in clearance | `engine.py` `LAGrid` | yes |
| Downwind consequence (people / hospitals / water plants within 10 km downwind) | `backend/la/consequence.py` | yes, context only |
| Live forecast (Open-Meteo) through all of the above | `backend/la/forecast.py` | yes, event `FX` |
| Live station gust nudging | `validation/common.py` `nudge_loso` | validation (V2) only |
| Two-step fire chain P(fault) x P(ignition), LANDFIRE fuel | `engine.py`, `calibrate.py` | **no**: lost on held-out ignitions |

## What is proven (held-out data)

| Claim | Evidence |
|---|---|
| Predicts from forecasts | V8: from the forecast issued a day before the Jan 2025 Eaton/Hurst storm, the fire alarm was knowable ~45 h before LA outages reached half their peak (69 h from the 2-day forecast; exploratory metric), and the lines nearest both ignitions ranked top 6% / 3% of 829 |
| Finds fire-starting lines | V5 (recorded weather): Woolsey top 1%, Eaton 10%, Hurst 5% |
| Agrees with NWS | V7: 95% of flagged line-hours were inside a Red Flag Warning |
| Outage timing | V4b: storm index AUC 0.81 vs gust-only 0.82 (tie). V8 2024: 0.80 vs forecast gust 0.86 (**loses**) |
| Weather inputs | V2: gust RMSE 3.4 -> 2.5 m/s with live-station nudging |

Pitch line that holds up: *"Highwire runs real physics on tomorrow's forecast. On the forecast issued the day before
the 2025 Eaton fire, it flagged the danger about two days before outages peaked and ranked the lines near both
ignitions in the top 6% of 829. One storm so far; every result, including ties, is published."*

## Decisions you should know about

- **Fire model choice used test results.** The two-step chain passed the pre-set training rule but did worse on held-out
  ignitions, so the product keeps the legacy score. Disclosed in REPORT.md. Switch back with one line in `validation/config.yaml`.
- **E3 "first spike" is distorted.** EAGLE-I data before Jan 6 2025 is mostly missing, so a tiny blip counts as the spike;
  the "half of peak" metric is reported as exploratory.
- **Forecast archive** (Open-Meteo Previous Runs) starts 2024-01-20. Every new windstorm after today is a clean test.
- Full reasoning: `validation/DECISIONS.md`. Every audit item and the **discarded-ideas pile**: [`AUDIT.md`](AUDIT.md).

## Next steps (priority order, from AUDIT.md)

1. **Resident reports on the real LA grid + responder alert.** `LAState.reports_view()` returns `[]`; snap reports to LA lines
   by coordinates; fix the circular corroboration in the synthetic `_place`; let a corroborated report raise that line's P(fault);
   list live/dead lines near reports in the bulletin.
2. **Rank plans by people downwind** x risk removed vs customers cut off (`consequence.py` already computes the numbers).
3. **More nearby data:** OSM schools, nursing homes, fire stations (one Overpass query) into the downwind cone.
4. **Gemini aerial-photo agent: built in v4 (`site_agent/`).** Set `GEMINI_API_KEY`, run `make site` so the agents get
   cached descriptions, and hand-label ~30 photos to measure Gemini's land-cover accuracy.
5. Re-run V8 as the forecast archive grows; consider re-testing the two-step fire model on new storms.

## Gotchas

- `D.calibration()` is cached by file modification time; don't mutate the returned dict.
- Line geometry in `grid_la.json` is `[lat, lon]`, not `[lon, lat]`.
- The storm model is fit on the 0.25 deg 2019-2024 series; replays use the 0.1 deg event grids (checked: not a problem).
- LANDFIRE samples are aligned to the current tower order. If you rebuild the grid, rerun `fetch_landfire.py` (in `make grid`).
- Big regenerable caches (`validation/results/series_*_2019_2024*.csv`) are git-ignored; the first `make validate` rebuilds them.
