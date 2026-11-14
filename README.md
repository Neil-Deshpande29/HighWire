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
  numbers appear on the app's **Evidence** page. Every number is read from `validation/results/*.json`.
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

Open http://localhost:5173. See **The screen** below.

## The screen

One view of the real LA grid at one moment, styled like a light-mode map app. The map fills the window; panels float on top.

- **Top bar.** The **clock** shows the moment on screen, with a badge: *Live forecast · updated <time> · Refresh* (the
  default, "Now", from the last saved Open-Meteo forecast, so it works offline) or *Recorded weather · <event>*.
  **Go to** jumps to a past event in plain words (Eaton & Hurst fires · Jan 2025, Woolsey fire · Nov 2018, Saddleridge
  fire · Oct 2019, two heat waves); **Back to now** returns. **Operate | Study** switches modes; **Evidence** opens the
  validation page.
- **Map.** One color layer with three levels (normal / elevated / act now): fire risk on wind days, line loading on heat
  days, storm damage from the **Layers** menu (which also toggles Red Flag zones, hospitals, faint wind arrows and labels
  on the ~8 riskiest lines). The camera frames the risk area when the clock jumps. **Hover** a line for a one-line
  summary; **click** a line for *Turn off / Turn on* and *Limit power* (cap at X% of rating), or a substation for
  *Shed X MW*.
- **Your changes tray** (bottom left). In Operate, clicked changes are staged here, not applied, with live physics from
  `POST /la/simulate`: customers losing power, hospitals on backup, high-risk lines still on, pass/fail. *Compare with
  Co-Pilot* shows them as a **Your plan** card next to the agents' plans.
- **Situation card** (right). One plain headline built from the data (no LLM), up to four big numbers (lines at high fire
  risk, people living downwind of them, hours to the worst hour, customers out now or forecast peak demand), *What's
  driving this* for the details, and **Get Co-Pilot plans**.
- **Plans.** One chip per agent while they work (the raw log is in *Agent log*). Each card starts with one sentence
  saying what the plan does, then the same four numbers: high-risk lines turned off, customers losing power, hospitals
  on backup, physics check. Steps, reasons, confidence and the patrol list are under *Details*. Clicking a card previews
  it on the map (lines go dark, areas that lose power dim); Approve/Reject appear on the selected card. Approving shows
  the outcome and the responder bulletin (*Copy*).
- **Timeline** (bottom). Play/pause, drag to scrub, one plain curve: power lines at high fire risk each hour (the
  busiest line's % of rating on heat waves), with a "now" marker for the forecast. On past events, real ignitions stay
  hidden (no spoilers) until you approve a plan or click **Show what happened**; then the fire start appears on the map
  and timeline, plus a **Did Highwire get it right?** box. It leads with the substation that fed the circuit that started
  the fire (read from the official facility record) and whether your approved plan cut its power, then when the line
  nearest the origin was first flagged, its rank at the ignition hour (V5), the caveats, and the real outage peak.
- **Study mode** (purple tint). A sandbox on top of the current moment. A **Conditions** panel scales wind, humidity,
  temperature and demand; line and substation clicks apply immediately. Every change reruns the same physics through
  `POST /la/simulate`; no model is refit. The co-pilot can plan on the study state. Nothing touches the Operate state:
  leaving Study discards it, and *Copy to plan* moves the grid changes into Operate's tray.
- **Evidence page.** Three plain claims with one number each (ignition ranks, forecast warning time, agreement with
  NWS Red Flag Warnings), *Where we fall short* (including the cases where a simple gust rule beats our storm score),
  and the full V1-V8 cards and plots behind *Full results*. Every number is read from `validation/results/*.json`.

Backend routes added for the screen: `POST /la/simulate {event, t, actions[], conditions?}` (physics on a copy; the Operate
state is never touched), a `limit_line {target, pct}` operator-derate action, and optional `actions` / `conditions` on
`POST /la/copilot/run` (a study run is stored separately from Operate). Tests: `backend/test_study.py`.

**Not finished**

- *Your plan* can be compared but not approved: approving operator-built plans would need a new endpoint beyond the
  allowed additions. Approve a co-pilot plan to act.
- Study conditions change the physics (fire risk, loading, temperatures, outages) but not the storm-damage layer, the
  timeline curve or the "worst hour" number, which come from the precomputed replay for the unmodified weather.
- In the Eaton/Hurst replay on recorded weather, the first line crosses the fire alarm after both fires started (the
  honest V4a result), so the replay opens after the ignitions. The forecast-driven V8 test is the one that warned early.
- The rule-based planner sometimes returns two plans that switch the same lines; the UI shows one.
- Resident ground reports on the LA grid are still not built (AUDIT.md, "To finish").

## Rebuild everything from scratch

```bash
cp .env.example .env           # all keys optional (EIA bulk CSVs and Census PL files need no key)
make data                      # fetch 13 public sources -> data/raw (cache) -> data/processed
make grid                      # OSM/HIFLD -> LA grid model + sanity / plan-to-peak step
make calibrate                 # gust correction, load model, fire thresholds (TRAINING events); storm model (2019-2021)
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
files, so the agents can cite the validation but never compute numbers. Without `GEMINI_API_KEY` the rule-based
planner runs. That planner was also extended for the LA grid: fire-risk violations, and PSPS groups split by zone and
voltage tier.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | unset | Enables the deepagents LLM team (Google Gemini via LangChain); otherwise the rule-based planner runs. Free key: https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Gemini model id (see https://ai.google.dev/gemini-api/docs/models). |
| `AGENT_TIMEOUT_S` | `60` | Fall back to the rule-based planner after this many seconds or on any error. |
| `EIA_API_KEY`, `CENSUS_API_KEY` | unset | Optional. The fetchers use keyless bulk files. |
| `MOCK_PACE` | `0.35` | Seconds between rule-based log lines so the activity log visibly streams. |

---

## Context for the team (v2: wind agent, Gemini, validation changes)

### How the AI part works (LangChain + Gemini)

- **Stack:** `deepagents` (LangChain's agent harness, built on LangGraph) + `langchain-google-genai` (`ChatGoogleGenerativeAI`).
  One factory, [`backend/llm.py`](backend/llm.py), creates the model; it also loads `.env`.
- **What calls the API:** only two places. (1) The co-pilot team in [`backend/agents/team.py`](backend/agents/team.py):
  `create_deep_agent(model, tools, system_prompt, subagents)` builds a coordinator plus 9 specialist subagents (heat,
  wind, atmosphere, water, reports, brownout, fire, cascade, flood). The coordinator calls the built-in `task` tool to
  run subagents in parallel, each subagent calls our Python tools (`get_grid_summary`, `get_storm_damage`,
  `simulate_plan`, `get_event_context`, `get_validation_summary`...), then the coordinator returns plans as JSON.
  (2) The ground-report parser in [`backend/reports.py`](backend/reports.py) uses `with_structured_output` to turn a
  911-style text into `{kind, location, severity}`.
- **Under the hood** each LLM step is one HTTPS request to the Gemini API `generateContent` endpoint
  (`generativelanguage.googleapis.com`) via the `google-genai` SDK. A full co-pilot run is roughly 15-40 requests.
- **Guard rails:** the LLM never produces a number shown in the UI. Every plan is re-simulated by the physics engine
  (`verify_output`), crew staging comes from the calibrated storm model, and any error or a 60 s timeout falls back to
  the rule-based planner.

### Key setup

1. Get a free key at https://aistudio.google.com/apikey.
2. `cp .env.example .env` and set `GEMINI_API_KEY=...` (optionally `GEMINI_MODEL`, default `gemini-3.5-flash`).
   `.env` is git-ignored.
3. `./run.sh`. The log line `model gemini-...: planning with 9 specialist agents` means the LLM team is live;
   `rule-based planner (no GEMINI_API_KEY)` means it is not.

| | No key (default) | With `GEMINI_API_KEY` |
|---|---|---|
| Map, replay, charts, validation | identical | identical |
| Co-pilot plans | deterministic search over switching actions, templated agent messages | Gemini agents reason over the tools and write plans + disagreements in their own words |
| Plan safety | physics-checked | physics-checked (same check, LLM plans that fail are rejected or shown as not safe) |
| Ground reports | keyword rules | Gemini structured extraction (falls back to rules on error) |
| Cost / network | none | Gemini API calls; falls back to rules if offline or slow |

### What changed in v2 and why

- **Wind agent = storm-damage model** ([`backend/la/storm.py`](backend/la/storm.py)). The fire index answers "will a line
  start a fire?"; most big LA outages are wind and wet-storm damage. The new model: chance each span is knocked down
  (gust + vegetation clearance), share of the network under damaging wind and heavy rain, and a **damage stock** that
  decays as crews restore lines (time constant tau). Fit on 2019-2021 only (onset 10 m/s, tau 6 h, rain kept).
  In the app: the **💨 Storm damage** map toggle, a storm-index stat, a "lines most likely to be knocked down" list,
  a storm line in the model-vs-reality chart, and **crew pre-staging** in the co-pilot output.
- **Live station nudging** (V2): the wind agent corrects gridded gusts with live airport readings, tested
  leave-one-station-out.
- **Demand model** (V3): heat x time-of-day and inland-heat terms.
- **LLM provider:** Anthropic -> Google Gemini. Previously no key was ever configured and nothing loaded `.env`, so every
  co-pilot run so far used the rule-based planner.
- **New data (keyless):** Open-Meteo hourly precipitation 2019-2024, Open-Meteo point series at the 9 ASOS stations.

### Validation: before -> after (test data only)

| Check | Before | After | Honest read |
|---|---|---|---|
| V2 gust error, all hours | bias +2.1, RMSE 3.4 m/s | bias +0.7, RMSE 2.5 | real improvement |
| V2 gust error, windy hours | bias -3.7, RMSE 5.9 | bias -3.3, RMSE 5.3 | small improvement; local canyon gusts still missed |
| V3 heat-wave peak (LDWP / SCE) | -8.0% / -10.4% | -2.7% / -8.4% | LDWP now beats persistence; SCE still does not |
| V4b worst-outage-hour AUC 2022-24 | fire index 0.62 vs gust 0.82 (fail) | storm index 0.81 vs gust 0.82 | from fail to match; does not beat gust-alone |
| V4a co-movement inside test events | fire E1 0.13, E3 0.78 | storm E1 0.04, E3 0.43 | worse inside single events (not a grid-resolution issue; checked) |
| V1, V5, V6, V7 | unchanged | unchanged | fire model was not retuned |

Pitch framing that holds up: *fire risk tells you which lines to shut off (V5 ignitions ranked top 1-10%, V7 95%
inside Red Flag zones); storm damage tells you when the big outage hours come (matches the best simple baseline over
three years) and which lines to send crews to. We publish where we lose.* Full details: `validation/REPORT.md`,
`validation/DECISIONS.md` ("v2 improvements").

### v3: prediction mode and audit (see [`AUDIT.md`](AUDIT.md))

- **🔮 Live forecast** (top of the event list): pulls the current Open-Meteo forecast and runs every calibrated model over
  the next ~3 days (fire risk, storm damage, demand, line loading). Refresh button in the UI, or `make forecast`.
- **V8, prediction from archived forecasts:** the same models fed only what the forecast said 1-2 days ahead. For the Jan
  2025 Eaton/Hurst storm, the fire alarm was knowable 45 h (day-1 forecast) to 69 h (day-2) before LA outages reached half
  their peak (exploratory metric), and the lines nearest the Eaton and Hurst ignitions ranked top 6% and 3% from the
  day-1 forecast. Over 2024 the day-ahead storm index scores AUC 0.80, below the forecast's own max gust (0.86).
- **Downwind consequence:** each line over the fire alarm shows the people, hospitals and water plants within 10 km
  downwind.
- **Real vegetation:** LANDFIRE canopy heights replace guessed constants in the clearance physics; the two-step
  fault x ignition fire chain was tested and not adopted (worse on held-out ignitions).
- **Agents:** LA runs a 4-agent team in one wave, picks actions from a validated candidate list, and the rule-based planner
  runs in parallel as an instant fallback.

### Next steps that would most improve the numbers

2. Ground reports on the real LA grid and the responder loop (AUDIT.md, "To finish").
3. More stations (RAWS fire-weather stations in the canyons) for the nudging.
