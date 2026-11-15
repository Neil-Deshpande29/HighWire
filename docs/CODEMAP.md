# Rewire code map

Rewire is a wildfire and grid co-pilot for Greater Los Angeles (commit 66cdd65, 2026-11-14).
Public data is turned into a modeled transmission grid: 671 buses, 864 lines, 13,671 tower points.
Deterministic physics (DC power flow, IEEE 738, sag, blowout, clearance) gives every line a calibrated 0-1 fire index each hour.
A planner (Gemini deepagents team, or a rule-based fallback) proposes switching plans; `verify_output` re-simulates every plan.
A FastAPI backend holds everything in memory; a React + three.js screen shows one hour at a time (Operate or Study).

## 1. Overall flow

```
public sources --data/fetch/*.py--> data/processed/*            (data/raw/ is git-ignored)
                      |
   data/build_grid.py + data/sanity_grid.py --> grid_la.json --fetch_landfire.py--> landfire_towers.npz
                      |
   backend/la/engine.py (LAGrid, hindcast) + backend/thermal.py + la/storm.py + la/load_model.py
        |                                            |
   validation/calibrate.py --> calibration.json      |
   validation/run_all.py (V1-V8) --> validation/results/*.json, REPORT.md
   validation/build_replays.py --> replay_<EID>.npz      backend/la/forecast.py --> replay_FX.npz
        |                                            |
   backend/la/replay.py (recorded hour)        backend/la/state.py (LAState: one hour, live physics)
        |                                            |
        +------> backend/agents/brief.py (build_brief) <------ site_agent/ (facts.py, naip.py, agent.py)
                      |
   backend/agents/team.py (Gemini team)  or  agents/mock_planner.py (rules)  --> agents/tools.py verify_output
                      |
   backend/app.py (FastAPI; in-memory LA, JOBS, _BASE)
                      |
   frontend/src: App.tsx -> la/LAApp.tsx -> la/LAScene.tsx + ui/*.tsx
```

## 2. Data flow, stage by stage

**Data files.** `data/fetch/*.py` (shared helpers in `data/fetch/common.py`; run by `make data`) write `data/processed/`:
`terrain_la.npz`, `weather/<EVENT>.npz` (0.1 deg, 8 x 13 cells, padded before and after the event), `weather/series_*.npz`, `asos_la.csv.gz`, `demand_la.csv.gz` (EIA-930), `outages_la_hourly.csv.gz` (EAGLE-I), `events/fires_wfigs.json`, `redflag_la.json`, `plants_la.csv`, `population_la.csv` (2020 Census tracts), `hifld_lines_la.geojson`, `landfire_towers.npz`.
Hand-written facts: `data/processed/events/fires.yaml`, `events/sce_psps_2025_01.yaml`, `validation/events.yaml`, `validation/config.yaml`, `config/region_la.yaml`.
All reads go through `backend/la/data.py`: `grid()`, `terrain()`, `weather(name)`, `demand()`, `outages()`, `events()`, `config()`, `fires()`, `redflag()`, `population()`, `calibration()`, `results_dir()`.

**Grid build.** `data/build_grid.py` `main()` reads OSM lines, EIA-860 plants, Census, terrain; writes `grid_la.json` and `grid_report.md`. Electrical values are modeled from voltage class (`CLASS`). Load is given to the nearest substation by tract population.
`data/sanity_grid.py` `main()` drops islands with no source and raises ratings above 90% at peak ("plan to peak"); rewrites `grid_la.json`. `make grid` runs both, then `fetch_landfire.py`.
Grid keys: `buses`, `branches` (`geometry`, `towers`, `kv`, `cable`, `rating_a`), `generators` (45), `imports` (32), `critical_sites` (153 hospitals, 65 water; each has `bus`, `backup_hours`).

**Physics engine.** `backend/la/engine.py`:
- `LAGrid`: arrays per branch and per tower (`tb`, `tlat`, `tlon`, `tzone`, `tfuel`, `tcanopy`); `ptdf(top)`, `injections(...)`, `flows(P, top)`, `islands(top)`, `base_topology()`.
- `tower_weather(grid, wx, hours_idx)`: bilinear weather at towers plus gust correction.
- `hindcast(grid, wx, hours_idx, ldwp_mw, sce_mw, top, shed, ...)`: returns arrays `line_risk`, `loading`, `line_temp`, `line_clear`, `line_gust`, `line_rh`, `flow_mw`, `region_index`, `energized`.
- Fire index per tower: `wind_term` x dryness x clearance x zone fuel (`FUEL`). A line's risk is its worst tower. `wind_pressure` is the opt-in v4 wind term.
- `backend/thermal.py`: `conductor_temperature`, `sag`, `blowout_angle`, `clearance`, `CONDUCTORS`.
- `backend/la/storm.py`: `hazards`, `index_from_hazards`, `event_storm` (storm-damage index and per-line damage probability).
- `backend/la/load_model.py`: `predict_from_weather` (demand for the forecast). `backend/la/consequence.py`: `downwind`, `downwind_union` (people, hospitals, water plants in a 10 km, 30 deg cone).

**Calibration and validation.** `validation/calibrate.py` `main()` runs `fit_gust`, `fit_load`, `fit_nudge`, `fit_storm`, `fit_fire` on training data only; writes `data/processed/calibration.json` and `validation/results/calibration_summary.json`.
Live values: fire model `legacy`, `g50` 6, `h50` 10, `c50` 5, `line_alarm` 0.3, `region_alarm` 268; storm `onset` 10, `tau` 6, `alarm` 0.052.
`validation/run_all.py` `main()` runs `validation/checks/v1_thermal.py` to `v8_forecast.py` (each has `run()`), then `build_replays.main()`, `summary_cards()` (saved as `results/summary.json`) and `write_report()` (writes `validation/REPORT.md`). Helpers live in `validation/common.py` (`event_hindcast`, `pair_station_grid`, `nudge_loso`, `series_storm`).

**Replay files.** `validation/build_replays.py` `build(eid, grid)` writes `validation/results/replay_<EID>.npz`: per hour x line arrays (`line_risk`, `loading`, `line_damage`, ...), weather grids (`wx_gust`, `wx_rh`, `wx_dir`), `outages`, `storm_index`. E3 is 120 hours x 864 lines.
`backend/la/forecast.py` `refresh()` = `fetch()` + `build()`: writes `weather/FX.npz`, `replay_FX.npz`, `forecast_FX.json` (event id `FX`).
`backend/la/replay.py` serves them: `load`, `events_list`, `static`, `hour`, `series`, `fire_checks`, `validation_summary`, `event_context`.

**LA state.** `backend/la/state.py` `LAState(eid, t, grid)` is the grid at one hour with live physics.
- Holds `lines`, `buses`, `weather`, `operator_opened`, `deenergized`, `shed`, `caps`, `conditions`, `plans`, `plan_status`, `bulletin`, `sim`.
- `simulate(actions)` runs `E.hindcast` for one hour and returns `{lines, buses, totals, violations, watch, is_safe, region_index}`. Nothing is mutated.
- Action types (`_topology`): `open_line`, `close_line`, `deenergize_lines` (`targets`), `deenergize_bus`, `shed_load` (`mw`), `limit_line` (`pct`), `dispatch_gen` (import points only).
- Policy: line under 180 kV over the alarm is a violation; 220-500 kV over the alarm is a `watch` item; overload, over-temperature, clearance under 3 m and more than 10% load unserved are violations.
- `apply_actions` mutates and recomputes. `with_conditions(cond)` returns a Study copy with scaled inputs. `is_modified`, `fire_lines`, `weather_extremes`, `view` feed the brief and the API.

**Situation brief.** `backend/agents/brief.py` `build_brief(state)` returns one dict of facts: `fire`, `storm`, `grid`, `weather`, `site`, `trust`, `headlines`. `brief_text(brief)` is the compact JSON for prompts. Details in section 5.

**Agents and tools.** `backend/agents/team.py` `run_team(state, log)` is the single entry point. `backend/agents/tools.py` has `make_tools`, `verify_output`, `summarize_result`, `JobLog`. `backend/agents/mock_planner.py` has `run_mock` and `_Planner`. `backend/llm.py` has `enabled`, `model_name`, `chat_model`. Details in section 4.

**API routes.** `backend/app.py` only wires the pieces together: `backend/routes_la.py` (the LA grid; holds `LA` (`state`, `grid`, `study`), `_BASE`, `FORECAST_JOB`), `backend/routes_synthetic.py` (the toy region, `STATE`), `backend/jobs.py` (`JOBS` and the one job runner both regions share) and `backend/bulletin.py` (responder bulletins). Table in section 3.

**UI.** `frontend/src`. Details in section 6.

## 3. Who calls what

| API route | Backend function | Touches |
|---|---|---|
| `GET /events` | `RP.events_list` | `events.yaml`, `v4a_replay.json`, live `FX` |
| `GET /la/static` | `RP.static` (cached forever) | grid, terrain, Red Flag zones, fires, calibration |
| `GET /replay/{event_id}?i=` | `RP.hour`, then `LA["state"].view()` as `copilot` if same event and hour | replay npz |
| `GET /la/series/{event_id}` | `RP.series`, `RP.fire_checks` | replay npz, `v4a_replay.json`, `v5_ignition.json` |
| `POST /la/simulate` | `la_simulate` -> `_base_state` -> `with_conditions` -> `_sim_payload` | `_BASE` cache only; never `LA["state"]` |
| `POST /la/copilot/run` | `la_copilot_run` -> new `LAState` -> `jobs.start` (thread) -> `run_team` | sets `LA["state"]` (Operate) or `LA["study"]`; `JOBS` |
| `GET /copilot/{job_id}` | `copilot_status` -> `jobs.view` | `JOBS` (log, output) |
| `POST /la/plans/{plan_id}/approve` | `la_approve` -> `apply_actions` -> `bulletin.la_bulletin` | `LA["state"]` only; 400 if the plan is not safe |
| `POST /la/plans/{plan_id}/reject` | `la_reject` | `plan_status` |
| `GET /la/state` | `la_state` | `LA["state"].view()` |
| `GET /la/site/{line_id}?event=&i=` | `la_site` -> `SA.describe` (may fetch photos and call Gemini) | `data/processed/naip/` |
| `GET /la/site/{line_id}/photo/{n}.jpg` | `la_site_photo` -> `naip.cached_tile` | cached JPEG |
| `POST /la/forecast/refresh`, `GET /la/forecast/status` | `FCST.refresh` in a thread | FX files; clears FX entries in `_BASE` |
| `GET /validation`, `/report`, `/results/*` | `RP.validation_summary`; `REPORT.md`; static mount | `validation/results/` |
| Toy region: `/state`, `/terrain`, `/scenario`, `/lines/{line_id}/toggle`, `/reports`, `/reset`, `/copilot/run`, `/plans/...`, `/bulletin` | `STATE` in `backend/state.py`, `backend/physics.py` | still served; no UI calls them |

| UI action | API call (`la` in `frontend/src/la/api.ts`) |
|---|---|
| Page load | `la.static()`, `la.events()` |
| Choose event ("Go to") | `la.series(eid)`, then `la.hour(eid, i)` |
| Scrub or play the timeline | `la.hour(eid, i)`; Operate also `la.simulate` (debounced, not while playing) |
| Click a line or substation, pick a control | `la.simulate(eid, t, actions)` for the "Your changes" tray |
| Study sliders or Study changes | `la.simulate(eid, t, actions, conditions)` |
| "Get Co-Pilot plans" / "Compare with Co-Pilot" | `la.run(...)`, then `la.job(id)` every 900 ms |
| Select a plan card | `la.simulate(eid, jobT, plan.actions)` to preview it on the map |
| Approve / Reject | `la.approve(id)` / `la.reject(id)` |
| Refresh (live forecast) | `la.refreshForecast()`, then `la.forecastStatus()` every 2 s |
| Evidence page | `la.validation()`, `la.result(file)` |
| (nothing mounted) | `la.site(...)`, `siteImageUrl(...)`: only `SitePanel.tsx` uses them |

## 4. The agent team

- **Entry.** `run_team`: if `LLM.enabled()` is false (no `GEMINI_API_KEY`; the case today) it returns `run_mock`. Otherwise it runs `_run_llm` and `run_mock` in two threads.
- **Coordinator (LA).** `LA_COORDINATOR_PROMPT` plus the brief. Tools: `get_candidate_actions`, `simulate_plan`, `get_event_context`, `get_validation_summary`, `get_critical_sites`, `get_site_context`.
- **Specialists (`LA_SUBAGENTS`).** `fire_agent` (`get_grid_summary`, `get_event_context`, `get_site_context`); `wind_agent` (`get_storm_damage`, `get_grid_summary`, `get_site_context`); `heat_agent` (`get_grid_summary`, `get_weather`); `brownout_agent` (`get_grid_summary`, `get_critical_sites`, `get_site_context`). Each gets `SUB_PROMPT` plus the brief and returns findings JSON.
- **Other tools.** `get_situation_brief` and `get_reports` exist in `make_tools` but no LA agent is given them (the brief is pasted into the prompts by `_la_prompts`). `SUBAGENTS` and `COORDINATOR_PROMPT` are the nine-agent toy-region team.
- **Rule-based fallback.** `_Planner.run`: `analyze` (templated findings and log lines), `storm` (crew staging), `candidates` (at most 14, parsed from violation strings), `greedy` (repair, up to 3 attempts), `brute_force` (1-3 action combos, capped at 150 simulations on LA), `plan_json`, `disagreements`. `run_mock` then calls `verify_output`.
- **`verify_output(state, output, log)`.** Re-simulates do-nothing and every plan. Keeps at most 3 steps and 3 plans. Overwrites `result`, `actions`, `id`; sorts safe plans first; sets `do_nothing`, `no_safe_option`, `least_bad`; attaches `brief`. `run_team` then overwrites `crew_staging` from `_Planner.storm`.
- **Timeouts.** `TIMEOUT_S` 50 s (`AGENT_TIMEOUT_S`); Gemini call timeout 40 s, 1 retry; `recursion_limit` 120; backup join 30 s. On timeout, error or bad JSON the parallel rule plan is returned with `fallback_reason`. `MOCK_PACE` 0.12 s sleep per log line.
- **The model can influence:** which actions are in a plan, step `why`, `raised_by`, `title`, `tradeoffs`, `assumptions`, `situation`, `disagreements`, and two numbers: `confidence` (clamped to 0-1) and `attempts`. `PlanCard.tsx` shows both; this breaks the "no LLM numbers" rule once a key is set. LLM text is not checked for digits (only the site agent sanitizes). `title` goes into the bulletin.
- **The model cannot influence:** any physics result, safety verdict, plan order or ids, the brief, crew staging, or approval (`la_approve` refuses unsafe plans; a human clicks Approve).
- **Hindsight.** The brief is clean, but `get_event_context` returns real fire names with hours to ignition, real outages and V5 cases; `get_storm_damage` returns real outages; `get_validation_summary` includes V5 and V8 headlines. LLM agents on a replay can see them.

## 5. Site agent and situation brief

**Site agent (`site_agent/`).**
- `facts.py`: `fire_points` picks up to 4 towers per line (fuel first, then elevation, at least 1.5 km apart). `line_facts(line_id, event_id, i, t)` returns `points`, `fuel_mix`, and per point `weather` and `downwind` for a replay hour. `alarm_lines()` lists lines that ever cross the alarm.
- `naip.py`: `tile_for`, `tiles_for`, `cached_tile`, `bbox`, `newest_item`, `crop_jpg`. Cache: `data/processed/naip/<line>-<n>.jpg` and `.json` (`item`, `photo_date`, `gsd_m`, `center`, `bbox`, `source`).
- `agent.py`: `describe(line_id, event_id, i, use_llm, chat, fetch, t)` returns `facts`, `photos`, `look`, `look_source`, `checks`, `notes`, `disclaimer`. One Gemini call covers all photos (`look_with_gemini`). `compact(out)` is the short form for agents.
- Rules: fixed vocabularies (`LAND_COVER`, `STRUCTURES`, `VEGETATION`, `ACCESS`, `CONFIDENCE`); `_clean_text` withholds any text with digits or number words (`NUMBER_WORDS`); `cross_check(point, look)` lists disagreements with LANDFIRE and Census and never resolves them.
- Description cache: `data/processed/naip/<line>.look.json`, valid only if `cache_key` (photo ids, positions, prompt hash) and the model name match. Fake-chat output is never cached. `_line_lock` allows one Gemini call per line.
- Callers: `la_site` (live fetch and Gemini allowed); `get_site_context`, `mock_planner._site` and the brief (always `use_llm=False, fetch=False`, cache only).
- Today: no key, and only L220 has cached photos, so the agents see `look_source` "none: ..." for nearly every line.

**Situation brief (`backend/agents/brief.py`).**
- Inputs: `state.sim`, `RP.hour` (fire lines, storm, Red Flag zones), `D.calibration()`, downwind counts, `SA.compact` for the top 3 lines, validation cards in `TRUST_CHECKS` (`V4b`, `V7` only; V5 and V8 name fires).
- If `state.is_modified()` (Study or operator changes), fire lines, region index and weather come from the state's own physics. Storm and Red Flag stay as recorded.
- Output lists are capped at `MAX_LINES` (8). Shut-off candidates are under 180 kV; patrol corridors are 180 kV and above.
- Cache: on the state object, keyed by event, hour, opened lines, conditions, caps, shed and the newest `*.look.json` mtime.
- Reaches: every LA prompt, `output["brief"]` on every job, and the coordinator's first log lines. No mounted UI component reads it.
- Tests pin it: `backend/test_brief.py` checks exact key sets, under 6,000 characters, no `outages` / `fires` / `actual_customers_out` keys, and no fire name in the text.

## 6. Frontend

- **Shell.** `App.tsx` owns `eid`, `mode` (operate / study), `page` (map / evidence), events and static data. `ui/TopBar.tsx` has the clock, "Go to", mode toggle and Evidence link.
- **State.** Almost all of it is `useState` in `la/LAApp.tsx`: hour index `i`, `hour`, `series`, Operate job and `view`, `staged` actions, Study actions and `cond`, selected plan and line, popover, layers. There is no store. The rules for what is shown are pure functions in `la/derive.ts` (`lineColors`, `situationNumbers`, `fireChecks`, `agentChips`, `distinctPlans`, `riskFrame`, `lineHoverText`). `la/hooks.ts` has `useSim` (debounced `/la/simulate`) and `usePoll` (job polling). `ui/SituationDetails.tsx` is the body of the "What's driving this" drawer.
- **Layout regions (`styles.css`).** `.map-full` (canvas) with `.labels`; `.topbar`; `.map-ui-left` (`Legend`, `LayersMenu`, `ConditionsPanel` in Study); `.bottom-left` (`ChangesTray`, `Timeline`); `aside.side` (`SituationCard`, `PlanList` with `PlanCard`s, footer); floating `.hover-tip` and `.popover` (`LineControls`); `.evidence` (`EvidencePage`).
- **Map.** `la/LAScene.tsx`: `Terrain`, `LineTier` (3 voltage tiers, click and hover), `WindArrows`, Red Flag outlines, `Points` (substations, hospitals), `Flame` (after reveal), `Label`, `CameraRig`. Colors: `riskColor`, `loadColor`, `damageColor`, `C`.
- **Helpers.** `ui/format.ts`: `planSentence`, `actionText`, `plainViolation`, `mergeAction`, `offLines`, `headline`, `worstHour`.
- **Unmounted.** `la/SitePanel.tsx` (props `line`, `eid`, `i`) and `la/BriefPanel.tsx` (prop `brief`) are imported nowhere. Their CSS (`.site-*`, `.brief-*`) is not in `styles.css`. They still type-check.
- **Removed.** The toy-region client code that used to sit in `frontend/src/api.ts` (it had no screen). The file now holds `API` and the plan and job types (`Action`, `Plan`, `Job`, `CopilotOutput`, `Brief`).
- **Not shown.** `output.situation`, `disagreements`, `agent_findings`, `brief`, and most of `crew_staging` (only one chip).

## 7. What the latest commits changed or broke

**944d7bd (UI redesign).**
- Replaced the whole frontend with one screen; added `ui/*`; removed the What-if (toy region) tab, the Validation tab and the model chart.
- Backend added `POST /la/simulate`, `limit_line`, `LAState.with_conditions`, Study runs in `/la/copilot/run`, `downwind_union`, `series` fields `n_high_risk` and `fire_checks`, `backend/test_study.py`.
- Left behind: toy-region routes and client code with no UI.

**300e005 (merge of `gemini-site-agent-wind-fix`).**
- Present and tested: `site_agent/`, `brief.py`, the tools, prompts with the brief, planner site phrases, `/la/site` routes, `wind_pressure` (default stays `legacy`), V5 scoring on the named line.
- Broken by the conflict resolution: `SitePanel` and `BriefPanel` are not on screen and have no CSS. On the branch (050018f) `LAApp.tsx` mounted them as `<SitePanel line={selected} eid={eid} i={i} />` and `<BriefPanel brief={out.brief} />`.
- So nothing in the UI calls `/la/site`; photos are fetched only by `make naip`, `make site` or a direct API call. The string "open the line in the Site agent panel" in `agent.py` points at a panel that does not exist.
- Docs drifted: `HANDOFF.md` state section (old branch, "21 passing"), `README.md` and `site_agent/README.md` (panel, single `<line>.jpg`).

**66cdd65 (brief follows Study).**
- Added `LAState.is_modified`, `fire_lines`, `weather_extremes`; `simulate` returns `region_index`; `_sim_payload` reuses them; brief cache key gained conditions, caps, shed.
- Left: the brief's storm block and Red Flag zones are always the recorded hour. The cache key omits `deenergized` and `imp_override`, which `is_modified` does check.

**Checked on 2026-11-14.** `pytest backend site_agent`: 55 passed. `npx tsc --noEmit` and `vite build`: clean.

## 8. Where new work plugs in

**Vexcel imagery provider (Phase 1).**
- New `site_agent/vexcel.py` must offer what callers use from `naip.py`: `tiles_for(line_id, points, fetch)`, `tile_for(line_id, n, lat, lon, refresh)`, `cached_tile(line_id, n, lat, lon)`, `CACHE`, and tile dicts with `item`, `img`, `center`, `photo_date`, `gsd_m`, `bbox`, `source`.
- Route these callers through one provider switch: `agent.describe`, `agent._look_cache`, `brief._site_cache_stamp`, `routes_la.la_site_photo`, `naip.main`, `agent.batch`. Tests patch `naip.CACHE`.
- `agent.cache_key` uses only `item` and position: add the provider. Tile files are named `<line>-<n>` with no provider, so give Vexcel its own folder or `cached_tile` will hand back the NAIP tile.
- `agent.PROMPT` says "USDA NAIP aerial photos, each about 1.2 km across": make source and resolution format fields.
- `.gitignore` only covers `data/processed/naip/`. `SitePanel.tsx` prints "NAIP {photo_date}". Type `Site` is in `la/api.ts`.

**CoStar buildings (Phase 2).**
- Loader: add a function to `backend/la/data.py` (copy the mtime pattern of `calibration()` if the table can change while the server runs).
- Consequence layer: `consequence._points`, `downwind`, `downwind_union`. The returned dict is passed through unchanged by `replay._downwind`, `LAState.fire_lines`, `routes_la._sim_payload`, `facts.line_facts`, `agent.compact`, `brief._build_la` (`line_row`), and a log line in `_Planner.analyze`. TS types: `Downwind`, `TopLine`, `Hour` in `la/api.ts`; `BriefLine`, `Brief` in `frontend/src/api.ts`.
- Brief: extend `line_row` or add a block in `_build_la`; update the schema docstring, `_headlines`, and the exact-key assertions in `backend/test_brief.py`.
- Cross-check: add a rule to `agent.cross_check(point, look)`; put the CoStar type on the point in `facts._point` / `line_facts`. Results flow to `describe()["checks"]`, `compact()["disagreements"]`, the brief's `site` rows and the planner log.

**Building triage table (Phase 3).**
- Offline sweep: model it on `validation/build_replays.py` and add a Makefile target. Tower arrays for building-to-line assignment: `facts._towers()` or `LAGrid.tlat`, `tlon`, `tb`. KD-tree example: `LAGrid._bus_counties`.
- Line hazard per hour: recorded `RP.load(eid)["line_risk"][i]` (branch order of `D.grid()["branches"]`); live or Study `state.sim["lines"][id]["fire_risk"]`. Fuel at towers: `facts._towers()["recept"]`, `facts.fuel_label`.
- Outage side: who loses power is `sim["buses"][id]["unserved_mw"]` and `routes_la._sim_payload()["buses_out"]`. Tie buildings to the nearest substation, the same rule `build_grid.py` uses for load. Critical sites already carry `bus`.
- Agent access: add a tool in `make_tools` (`all_tools`) and name it in `LA_SUBAGENTS` / `coord_tools` in `_run_llm`. Store under a git-ignored `data/processed/` folder.

**Per-action warnings (Phase 4).**
- Candidates: `_Planner.candidates` builds `{action, why, raised_by, label}`; `get_candidate_actions` exposes them.
- Plan steps: `_Planner.plan_json` (rules) and the coordinator's JSON (LLM) both pass through `verify_output`, which keeps step dicts as they are. Compute and overwrite warnings there, so both paths get them and the model cannot set them. For staged user actions use `routes_la._sim_payload`.
- Lines an action turns off: `open_line.target`, `deenergize_lines.targets` (`offTargets` in `ui/format.ts`).
- Write-up subagents: follow `team._subagent_specs`; keep them out of the 50 s `run_team` budget; cache like `agent._look_cache`; reuse `agent._clean_text`; test with `FakeChat` from `site_agent/test_site_agent.py`.
- UI: `Plan` type in `frontend/src/api.ts`; step list in the Details drawer of `ui/PlanCard.tsx` (the card root has `onClick={onSelect}`, so stop propagation); `ui/ChangesTray.tsx` for staged actions.

**Costs and escalation (Phase 6).**
- Inputs that exist: `totals.unserved_mw`, `totals.customers_out` (`CUSTOMERS_PER_MW` = 500, modeled), `totals.critical_on_backup`, bus `critical_type` and `backup_hours`. No outage duration exists anywhere; it must be an explicit assumption.
- Where numbers surface: `summarize_result` (plan result), `routes_la._sim_payload` (tray and Study), `verify_output` (`do_nothing`), `bulletin.la_bulletin`, and the gate in `la_approve`.
- There is no audit log: `JOBS`, plans and the bulletin are in memory. A persisted log has to be added at `la_approve`, `la_reject` and `jobs._run`.
- Thresholds: `validation/config.yaml` via `D.config()`, or a new file in `config/`. UI: `planNums` and the four tiles in `PlanCard.tsx`; `SituationCard.tsx` shows at most four numbers.

**Map overlay (Phase 7).**
- `LAScene.tsx`: `Terrain` is a vertex-colored mesh with no UVs. `makeProj` (1 unit = 1 km, north is -z), `makeHeight`, `VEX`. Lines sit only 0.14-0.22 units above the terrain (`lift`), so the overlay must be on or under the terrain surface.
- Toggle: `Layers` type in `LAScene.tsx`, `toggle(...)` rows in `ui/LayersMenu.tsx`, `layers` state in `LAApp.tsx`.
- Image route: copy `la_site_photo` (FileResponse from a cache). Building markers: `Points`, `Label`, `PortalCtx`.
- Only a light theme exists (`:root` in `styles.css`, canvas background `#e9eef3`). There is no dark theme to check.
- To remount the panels: put them in `aside.side` in `LAApp.tsx` using `selLine`, `eid`, `i` and `job.output.brief`; port the CSS from 050018f.

## 9. Gotchas (each checked in the code)

- **Coordinate order.** `branches[].geometry` is `[lat, lon]`. Red Flag rings and fire perimeters are `[lon, lat]`. `naip.bbox` returns (min lon, min lat, max lon, max lat); tile `center` is `[lat, lon]`. `LAState.buses` use `x` = lon, `y` = lat.
- **Two hour indexes.** Replay index `i` counts hours of the replay file. `LAState.hour_idx` indexes the padded weather file. Same hour in E3: `i` 61, `hour_idx` 117. `line_facts` takes replay `i` or ISO `t`.
- **Caches keyed by mtime.** Only `D.calibration()` and the brief's site stamp. Everything else is `lru_cache` for the life of the process: `D.grid`, `D.weather`, `D.events`, `D.config`, `D.population`, `RP.static`, `consequence._points`, `facts._towers`. `RP.load("FX")` clears the whole replay cache on every call.
- **Global LA state.** One `LA["state"]` for all browser tabs. Each Operate run replaces it with a fresh `LAState`, so earlier approvals are gone on the server. `LA["study"]` is written and never read. `JOBS` is never pruned.
- **Reload watcher.** `run.sh` starts `uvicorn --reload --reload-dir backend --reload-dir site_agent`. Saving any `.py` there restarts the server and wipes `LA`, `JOBS`, `_BASE` and running jobs. Do not go back to a plain `--reload`: it also watches `.venv`, and anything that touches it (running the tests, a pip install) restarts the server in a loop while requests hang.
- **Violation strings are an interface.** Written in `LAState.simulate`; parsed by regex in `_Planner.candidates`, `_fix_for`, `_relief_for` and in `plainViolation`.
- **Hard-coded thresholds.** 180 kV is repeated in `state.py`, `brief.py` and `LAApp.tsx`. The line alarm is 0.3 from calibration, but `_Planner.analyze` hard-codes 0.3 and 0.7. Read `D.calibration()["fire"]["line_alarm"]`.
- **`GET /la/site/{line_id}` can block.** First use downloads up to 4 photos (60 s timeout per request) inside the HTTP request. It also returns 404 for `event=FX`.
- **Fire model switch.** `fire_model` in `validation/config.yaml` is read only by `fit_fire`. The engine reads `calibration.json`. Switching needs `make calibrate validate`.
- **LANDFIRE alignment.** If `landfire_towers.npz` `n_towers` differs from the grid's tower count, it is silently ignored by `LAGrid` and `facts._towers`.
- **Counts.** `grid_report.md` says 675 buses / 867 branches (before the sanity step). The file has 671 / 864. The coordinator prompt says "L1..L867".
- **UI hides, API does not.** `/replay` always returns `fires` and `outages`; only the UI waits for "Show what happened".
- **Weather is coarse.** 0.1 deg cells. Downwind direction is the nearest cell to the line's mean point (`fire_lines`, `replay._downwind`) or to the photo point (`facts.hour_weather`).
- **No key, no `.env`.** `backend/llm.py` loads `.env` on import. Tests run without a key or network.
- **Timings measured here.** `LAGrid()` 0.9 s, `LAState` 0.09 s with a shared grid, `simulate` 0.03 s, cold `build_brief` 0.7 s (3,171 characters). Compute is cheap; network and LLM calls are the slow part.

## 10. Added after this map was written (2026-11-14)

- `backend/la/buildings.py`: buildings behind each substation, by kind, with triage weights; `outage_impact`, `impact_of`, `kind_of`. `LAState.simulate` adds `totals.buildings_out`; `LAState.cut_impact(line_id)` says what one line feeds.
- `site_agent/greenery.py`: vegetated share of the 100 m corridor around a line, from its aerial photos (infrared when available). `site_agent/imagery.py` is the provider switch.
- `backend/la/basemap.py`: the stitched aerial picture under the map; routes `GET /la/imagery` and `GET /la/imagery/basemap.jpg`; `make imagery`.
- How CoStar and Vexcel data plug in: `docs/COSTAR.md`.

## 11. The operator desk, Vexcel and the Buildings agent (2026-11-14, evening)

**Right column** (`frontend/src/la/LAApp.tsx` holds the state; panels in `frontend/src/desk/` and `frontend/src/ui/`):
situation (`ui/SituationCard.tsx`, pinned) → middle, the only part that scrolls: options table and the selected
option (`desk/OptionsTable.tsx`), the agent team (`ui/TeamPanel.tsx`, with `desk/SynthesisGraph.tsx` and the priority
ladder), or one sheet (`desk/sheets.tsx`: Risk by line, Sensors, Who is affected, Log; `desk/LineSheet.tsx`) →
Approve bar (`desk/DecisionBar.tsx`: Approve, Reject with a reason, supervisor sign-off for big cuts) → timeline
(`ui/Timeline.tsx`). Every option's numbers come from `POST /la/simulate` through `useSims` (`la/hooks.ts`).

**Backend added** (all under `backend/la/` unless noted):

| File | What it does | Route |
|---|---|---|
| `sensors.py` | weather stations against the model's weather (recorded ASOS for replays, NWS live for Now) | `GET /la/sensors/{event}` |
| `exposure.py` | buildings downwind of lines left on at high risk; every simulation carries it | in `POST /la/simulate` |
| `risk.py` | per-line likelihood and the two consequences, plus the matrix | `POST /la/risk` |
| `authority.py` | who may approve; a big cut needs the supervisor role, enforced by the approve route | in simulate and plans |
| `record.py` | append-only log of runs, plans, approvals, rejections | `GET /la/record`, `POST /la/reset` |
| `feedback.py` | why plans were rejected, kept on disk for the coordinator | `POST /la/plans/{id}/reject`, `GET/DELETE /la/feedback` |
| `vexcel.py`, `trees.py` | Vexcel API client; trees near a line's wires | (cache only) |
| `basemap.py` | aerial picture under the map: Vexcel, USGS where Vexcel has none | `GET /la/imagery` |
| `buildings.py` | building kinds, weights, the five-tier ladder; reads the CoStar extract when imported | `GET /la/buildings` |
| `costar_import.py` | turns the CoStar extract into `data/processed/costar/buildings.json` | `make costar FILE=...` |
| `agents/synthesis.py` | how the team's findings became plans, as graph data | in the co-pilot output |

**Agent team**: Fire, Wind, Heat, Brownout and Buildings report to the coordinator. New agent tools in
`agents/tools.py`: `get_building_impact` (what each line feeds, by tier) and `get_operator_feedback` (earlier
rejections). The rule-based planner (`agents/mock_planner.py`) does the same in code: `buildings()`, `stations()`,
`feedback()`. A rejection reason of "too many customers" or "critical site" adds a smaller shut-off to the next run,
marked `from_feedback`; the physics check still decides whether it is safe.

**Runtime data, git-ignored**: `data/processed/imagery/`, `data/processed/vexcel/`, `data/processed/costar/`,
`data/processed/feedback/`. Tests never touch them (`backend/conftest.py`).

## 12. The control room (2026-11-14, night)

Replaces the layout described in sections 7 and 11. Styles: one dark theme, `frontend/src/theme.css` (`styles.css` and
`desk/desk.css` are gone); type is Barlow and Barlow Condensed, bundled through `@fontsource`.

- **Top bar** (`ui/TopBar.tsx`): name and region picker, the clock ("Now"), the scenario picker, a slot the timeline
  is drawn into (`ui/Timeline.tsx`, a keyboard slider), Operate / What-if, Evidence.
- **Left dock**: the agents. `ui/TeamPanel.tsx` with the live graph (`desk/SynthesisGraph.tsx`, driven by the run's
  log), the priority ladder, the agent log.
- **Middle**: the map (`la/LAScene.tsx`), with the surroundings picture (`backend/la/basemap.py build_surround`,
  `/la/imagery/surround.jpg`), the navigation pad (`desk/NavPad.tsx`), and blue marks on lines that pick up load.
- **Right dock**: Grid status (`desk/StatusBox.tsx`), The plan (`desk/PlanPanel.tsx`), then Who is affected,
  What really happened, Risk by line, Sensors, Log.
- Every box in both docks is a `desk/Section.tsx` drop-down; `boxes` in `la/LAApp.tsx` holds which are open.
- **Agents first.** In Operate, `LAApp.tsx` starts a run 0.7 s after the clock rests on an hour; What-if waits to be asked.
- **Impact of a switch.** `lineImpact` in `la/derive.ts` compares the grid before and after (plan preview, your
  changes, or a what-if switch) and lists the lines whose load rose by 3 points or more.
- **Cost.** `backend/la/costs.py` adds `cost` to every `/la/simulate` answer: customers cut off times LBNL ICE
  Calculator 2 rates per interruption. The business share (1 in 8) is an assumption and the screen says so.
- **Heat days.** `greedy()` in `backend/agents/mock_planner.py` tries moves that cut nobody off, then trimming demand
  where it relieves the overloaded line most, before switching that line off (up to `HEAT_MOVES`).

## 13. The LED strip (2026-11-14, night)

Optional hardware: a 30-LED strip that stands for the grid, west to east, and follows the map.

- The server drives the strip from the system's own state, by itself. `backend/routes_la.py` `_strip_scene` works out
  the scene: green when no line is in trouble; a red stretch where lines are at high fire risk or overloaded (placed by
  their longitude); that stretch dark once power is cut by an approved plan or by hand. `_strip_refresh` is called
  where the system changes: at the start of an approval (before its slower work) and again when it is done, on a
  reset, and when the agents start on an hour. On the real strip the dark scene arrives about 40 ms after Approve.
- The screen (`LAApp.tsx`) only tells the server which hour it shows and what was switched by hand (`POST /lights`).
- `backend/lights.py`: `scene_of` is the rule; a background thread sends each scene as one text line (`grid normal`,
  `grid event A B`, `grid cut A B`) to the ESP32 over USB serial, repeating it every 5 s because the wire to the strip
  is one-way. The port is opened when the server starts. When the strip's own board is plugged in by USB too, the
  scene also goes straight to it, its replies show whether the ESP32's wire carries anything (`GET /lights`, "wire"),
  and a wire that stays silent gets the ESP32 restarted. No strip, or no pyserial: a quiet no-op.
  `python -m backend.lights demo` plays the four steps without the app.
- `hardware/lights/`: the two boards' MicroPython programs (the `grid` command was added to both) and a README.
- `backend/test_lights.py` runs both board programs on fake hardware against every line the app sends.
- `conftest.py` (repo root): empties the API keys and turns the strip off for every test, so a real key in `.env`
  never reaches a test.

Also that night: the Google AI key went into `.env`. A quota refusal from Google is now said in plain words and the AI
team is not asked again until the limit resets (`backend/agents/team.py`, `quota_refusal`).

After the Codex adversarial review that night (three findings, all reproduced and fixed):

- A plan is never cut short when it is verified. The rule-based planner may use up to 6 steps (`MAX_STEPS`), the AI
  team 3 (`MODEL_STEPS`); a longer plan is dropped whole (`backend/agents/tools.py`, `verify_output`). Before, a
  five-step heat plan was checked on its first three steps and shown as "no safe plan". Sampled heat-wave hours with a
  safe plan went from 11 of 29 to 25 of 29.
- Every cached answer on the screen is keyed by the forecast it came from (`rev` in `LAApp.tsx`), so "refresh
  forecast" never reuses the old weather's physics.
- A cut only counts demand still being served (`LAState.served_mw`): demand already shed is not lost twice in
  `cut_impact` or the risk list.

## 14. The agent team driven by rules (2026-11-15)

- `backend/agents/scripted.py`: `RulesModel` is a chat model with no model in it. It plays the orchestrator and every
  specialist through the real deepagents graph, taking each step from a script (`build_script`) that the rule-based
  planner works out for the hour. `run` returns the planner's output after the graph has acted it out.
- `backend/agents/team.py`: `planner_choice` reads `AGENT_PLANNER` (`agents` default, `gemini`, `rules`);
  `run_rules_team` runs the scripted team and re-checks it with physics, and hands over to the plain rule-based planner
  if the graph cannot be run. With `gemini`, the rule-driven team is the parallel backup.
- The callback (`_make_callback(..., trusted=True)`) shows the rule-driven team's words as written: code wrote them,
  so the number filter for model text does not apply.
- `simulate_plan` takes up to six actions (`MAX_STEPS`), so a heat-day plan can be judged whole.
