# Highwire audit: status of every item

Written for: the Highwire team. Audits the external review ("Ignition mechanism", "Consequence layer",
"Engineering and performance", "Pitch vs build") against the code on branch `v2-storm-forecast`.

Legend: **DONE** (fixed and validated), **PARTIAL**, **DEFERRED** (worth doing, not done yet; see "To finish"),
**DISCARDED** (pushed back; reason in the Discarded ideas pile, do not re-propose unless the reason changes),
**LIMIT** (cannot be fixed with public data; stated in the report and pitch).

## Results after the audit (held-out data)

| Check | Before audit | After | Read |
|---|---|---|---|
| **V8 prediction, E3 Eaton/Hurst, day-1 forecast** | not tested | fire alarm knowable **45 h** before LA outages reached half their peak (day-2 forecast: 69 h)* | the forecast saw it coming |
| V8 ignitions from the day-1 forecast | not tested | Eaton line top 6%, Hurst top 3% of 829 lines (gust-only 2%, 10%) | day-2: Eaton 11%, Hurst 87% |
| V8 2024 day-ahead storm index, worst outage hours | not tested | AUC 0.80 (same index on recorded weather 0.77) | **loses** to the forecast's own max gust (0.86) |
| V4b worst outage hours 2022-24 | fire 0.62 vs gust 0.82 | storm 0.81 | matches, does not beat |
| V5 ignitions (recorded weather) | top 1% / 10% / 5% | unchanged (legacy fire model kept) | |
| V2 gust error | RMSE 3.4 | 2.5 (live-station nudging) | |

\* "Half of peak" is the exploratory metric (added after seeing plots in v1). The pre-set "first 3 h above 2x baseline" metric
is distorted in E3: the EAGLE-I window before the event is mostly missing, so a ~1,000-customer blip on Jan 6 counts as the spike.

**The two-step fire chain was built and tested but is not the product default.** It passed the pre-set training rule, yet
on held-out data it ranked the test ignitions worse (Woolsey 1% -> 5%, Hurst 5% -> 23%) and its E3 shutoff counties no
longer matched SCE's (Jaccard 0.75 -> 0.00). The product keeps the legacy fire score (`validation/config.yaml: fire_model`);
that choice used test results, which REPORT.md discloses. Two-step results: `validation/results/two_step_v3/`.


## 1. Ignition mechanism

| # | Review item | Status | What we did |
|---|---|---|---|
| 1 | Fault and ignition blended into one score | **BUILT, NOT ADOPTED** | Built as a chain (switch with `fire_model: two_step`): `P(fault | wind, vegetation)` x `P(ignition | humidity, fuel)`. The fault step *is* the storm-damage model fit on real outages (2019-2021); only the humidity half-point of the ignition step is fit (training events). `backend/la/engine.py` (`damage_prob`, `fuel_receptivity`), `validation/calibrate.py` (`fit_fire`). |
| 2 | Fuel scored by elevation band | **BUILT, NOT ADOPTED** | Used by the two-step chain above (the legacy score keeps zone fuel). LANDFIRE 2016 FBFM40 fuel model sampled at every tower and 45 m either side (13,671 towers x 3 points), mapped to spark receptivity (grass 1.0 ... urban 0.05). `data/fetch/fetch_landfire.py`. |
| 3 | Tree geometry wrong | **PARTIAL** | Real LANDFIRE vegetation height (EVH) replaces the per-zone canopy constants in the clearance physics and the storm (fault) model (mean 0.8 m at towers vs the old 8.2 m guess; only 1.6% of towers have trees taller than the right-of-way half-width). Explicit fall-in (tree height vs distance) is not modeled: 30 m pixels cannot place a tree relative to the conductor. |
| 4 | No spread / consequence term | **PARTIAL** | Downwind consequence layer (`backend/la/consequence.py`): people (2020 Census tracts) and hospitals / water plants in a 10 km, 30-degree cone along that hour's wind. Shown per over-alarm line in the UI and in the fire agent's findings. Kept separate from the probability, as the review asked. Not yet used to rank plans (DEFERRED). |
| 5 | No equipment condition | **LIMIT** | No public asset-condition data. Stated in REPORT limitations. |
| 6 | Validation targets mismatch the steps | **DONE** | Fault step vs outages (V4a/V4b storm index, V8 day-ahead); ignition chain vs ignitions (V5) and Red Flag Warnings (V7). Woolsey (16 kV) and Eaton (idle line) caveats are printed with V5. |
| 7 | Dead checks: "clearance < 3 m" never fires | **DONE (data)** | Fed real canopy data as the review suggested. With real LANDFIRE heights it fires on 1 of 207,360 line-hours in the two test storms (E1, E3; min clearance 2.9 m), because LA's lines mostly run over developed land. It now reflects data, not a guessed constant. |
| 8 | Distribution lines missing | **LIMIT** | Grid starts at 66 kV (OSM). Most utility ignitions are on distribution. Say so in the pitch. |

## 2. Consequence layer (data choices)

| Review idea | Status | Note |
|---|---|---|
| Google Earth Engine (Dynamic World, NAIP, Sentinel-2) | **DISCARDED** | See pile. LANDFIRE gives fuel type and vegetation height without a login. |
| Google Maps / Earth imagery | **DISCARDED** | Terms bar storing tiles or deriving data (the review says so too). |
| Building footprints (Microsoft / Overture) | **DEFERRED** | Multi-GB downloads; Census tract population used as the proxy for now. |
| More OSM categories (schools, nursing homes, fire stations) | **DEFERRED** | One Overpass query; add to the downwind cone. |
| Road network, one-way-out neighborhoods | **DEFERRED** | |
| Gemini vision `site_agent` reading an aerial tile | **DEFERRED** | Most demoable for a Gemini track; NAIP is keyless via Microsoft Planetary Computer. Needs a Gemini key to demo. |
| Rank plans by consequence-weighted risk removed vs customers cut | **DEFERRED** | Consequence numbers exist per line; planner ranking not changed yet. |

## 3. Engineering and performance

Profiled before deciding: one LA simulation takes **~0.07 s** (power flow 21 ms incl. an 8 ms matrix inverse, conductor
temperature 44 ms, tower weather 2 ms). Items that only shave milliseconds were pushed back.

| Review item | Status | What we did |
|---|---|---|
| 5 of 9 subagents are no-ops on LA | **DONE** | LA runs a 4-agent team (fire, wind/storm, heat, brownout); water, atmosphere, reports, cascade, flood stay on the synthetic What-if region. |
| Two sequential waves vs 60 s timeout | **DONE** | One parallel wave; the rule-based planner runs **in parallel** and is used instantly on timeout or error; timeout 50 s. |
| get_grid_summary computed 9 times | **DONE** | Computed once per grid state and shared (cached on the state). |
| LLM invents line IDs; toy examples in prompt | **DONE** | New `get_candidate_actions()` tool (real IDs, pre-screened); LA-specific coordinator prompt without toy G2/R3 examples. |
| simulate_plan returns every violation | **DONE** | Count + top 5. |
| MOCK_PACE 0.35 s | **DONE** | 0.12 s. |
| Gemini switch | **DONE** | `backend/llm.py`; team, report parser and `/state` mode all use it; `.env` is now actually loaded (it never was). |
| Cache tower weather / interpolation per hour | **DISCARDED** | 2 ms of 70 ms. |
| D.calibration() re-read per simulation | **DONE** | Cached, re-read only when the file changes. |
| PTDF re-inversion (LODF / Sherman-Morrison) | **DISCARDED** | 8 ms. |
| Bisection warm start | **DISCARDED** | 44 ms total; not the bottleneck for a demo. |
| Python loops in LAState.simulate | **DISCARDED** | A few ms. |
| Brute force capped at 60 sims | **DONE** | 150 (about 10 s worst case), so 3-action plans get tried. |
| Global LA state; JOBS never pruned | **DISCARDED** | Single-operator demo. Re-open for multi-user deployment. |
| /replay payload every 650 ms | **DISCARDED** | ~60 KB per hour on localhost; not a bottleneck. |
| Red Flag timestamps re-parsed per request | **DONE** | Parsed once. |
| *(found during audit)* thread race on `_last_imp` | **DONE** | The import override was passed through a shared attribute; the parallel backup planner would have raced the LLM planner. Now returned explicitly. |

## 4. Pitch vs build

| Pitch promise | Status | Note |
|---|---|---|
| Cause team (heat, wind, pressure, water) | **PARTIAL** | LA: heat + wind/storm + fire (+ brownout). Water/pressure only on the synthetic region (no LA flood data). |
| Ground reports on the real grid | **DEFERRED** | Biggest remaining gap. `LAState.reports_view()` returns nothing. |
| Reports pinned to nearest line (circular corroboration in toy) | **DEFERRED** | Fix `_place` so an unlocated report is not auto-placed on the riskiest line and then "corroborated" by it. |
| Reports raise risk on that stretch | **DEFERRED** | Bayesian bump of P(fault) for the reported line. |
| Outcome team: cascade, flood | **PARTIAL** | Synthetic region only. |
| Physics checks every plan | **DONE** | |
| Plain-language plans, 3D map | **DONE** | |
| Report back to 911 / responders | **DEFERRED** | Text bulletin only; nothing is sent. |
| Floodwater near substations (LA) | **DISCARDED** | No flood events in the LA scope. |
| "Built for small co-ops" | **REFRAME** | The demo is a large utility's 66 kV+ network. Pitch it as "transmission and sub-transmission today, distribution with utility data". |

## 5. Beyond the review: prediction (the goal)

| Item | Status | Where |
|---|---|---|
| Live forecast mode | **DONE** | `backend/la/forecast.py`: current Open-Meteo forecast (keyless), every calibrated model over the past 12 h + next ~3 days, shown as the **🔮 Live** event; refresh button in the UI, `POST /la/forecast/refresh`, `make forecast`. |
| Prediction test on archived forecasts | **DONE** | V8 (`validation/checks/v8_forecast.py`): same models, no refitting, driven by what the forecast said 1-2 days ahead (Open-Meteo Previous Runs API). |

## To finish (in priority order)

1. **Ground reports on LA** (pitch's second half): `LAState.reports_view`, snap reports to LA lines by coordinates, corroboration that is not circular, a corroborated report raising that line's P(fault), and a live/dead list in the responder bulletin.
2. **Consequence-weighted plan ranking**: rank plans by downwind people x risk removed vs customers cut off.
3. **Gemini site agent** (needs a key): `get_nearby_context(line_id)` with a NAIP tile (Planetary Computer) for the over-alarm lines only; output is operator context, never a physics input.
4. **More consequence data**: OSM schools / nursing homes / fire stations; building footprints if disk allows.
5. **Bigger prediction test**: extend V8 as the forecast archive grows; add more stations (RAWS) for nudging.

## Discarded ideas (do not re-propose unless the reason changes)

- **Google Earth Engine** (Dynamic World, NAIP, Sentinel-2): needs a Google Cloud project and login. Replaced by LANDFIRE + Census.
- **Google Maps / Earth imagery**: terms bar storing tiles or deriving data.
- **LANDFIRE LFPS bulk jobs**: require submitting an email address. Used LANDFIRE's keyless ArcGIS `getSamples` at tower points instead.
- **Quantile-mapping gust correction** (2019-2021 station climatology): improved all-hours error but worsened windy test hours (RMSE 5.9 -> 8.0).
- **Heat terms in the storm-damage model**: lowered leave-one-year-out AUC (2019-2021).
- **Re-fitting the storm model on a 0.1 degree grid**: tested on a training event; grid resolution is not why V4a is weak (0.49 vs 0.51).
- **Feeding nudged gusts into replays**: would require refitting the fire thresholds on nudged training data; nudging stays a V2 result.
- **Engine micro-optimizations** (LODF, bisection warm start, per-hour weather cache, vectorizing simulate loops): a simulation is ~0.07 s.
- **Per-session state / JOBS pruning, smaller /replay payloads**: single-operator localhost demo.
- **LA flood modeling**: no LA flood events or flood data in scope.
- **Building distribution lines from OSM `minor_line`**: too sparse in LA to be credible.
