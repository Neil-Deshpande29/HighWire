# Decisions log

Every detail the Phase-2 spec left open, the choice made, and why. Newest decisions that changed results after a
first run are marked **(post-hoc, disclosed)**.

## Data
- **EAGLE-I covers 2025.** The figshare dataset now includes `eaglei_outages_2025.csv`, so E3 is validated against
  EAGLE-I as well as the CPUC PSPS report. National files (1-1.4 GB each) are streamed and filtered to FIPS 06037,
  06111, 06059, 06071; only the filtered files are kept. Timestamps are treated as UTC (the Jan 8 2025 LA outage surge
  lines up with the 18:18 PST Eaton ignition only under UTC).
- **EAGLE-I aggregation (post-hoc, disclosed).** First run used the hourly max of 15-min runs; it picked up glitches
  (single runs of 2,632 between 73,000s; runs of ~1.7 M LA County customers on Jan 8 2025, about half of all customers
  and far above every public report). Switched to the hourly median and drop LA County runs > 1,000,000.
- **EIA-930 without a key.** No `EIA_API_KEY` was available, so demand comes from EIA's public six-month bulk CSVs
  (same data). EIA-930 starts July 2018. We use "Demand (MW) (Adjusted)" when present.
- **Census API needs a key now.** Keyless requests return "Missing Key", so tract population and internal points are
  read from the PL 94-171 California geographic header (`cageo2020.pl`, POP100/INTPTLAT/INTPTLON, summary level 140).
- **HIFLD.** HIFLD Open is gone; an ArcGIS-hosted copy of the transmission layer answered (180 features in the bbox)
  and is used only to fill missing OSM voltages (125 ways filled).
- **OSM towers not downloaded.** Line geometry already traces tower positions; we sample our own tower points every span.
- **Red Flag zone polygons** come from IEM's per-event geojson (the zones as they were); api.weather.gov 404s on retired zone codes.
- **Fire origins.** WFIGS incident locations, filtered by name AND date window (EATON/HURST have older same-name fires).
  Woolsey has two records; the CALAC one stores local time as UTC (06:35 PST, before the fire), so the CAVNC record is used.
- **Ignition times** use utility/agency-reported times (Woolsey 14:22 PST circuit lockout; Hurst 22:10 PST, SCE EIR) and
  WFIGS discovery otherwise; every fact has a `source_url` in `data/processed/events/fires.yaml`.
- **SCE PSPS (Jan 2025).** Only event-level totals and the de-energized county list could be extracted from the PDF; the
  per-circuit table lives in Attachment C (Excel), which was not reachable. V6 is therefore county-level only.
- **Tree canopy: per-zone constant** (mountain 6 m, valley 10 m, basin 9 m, coast 7 m). NLCD/Meta canopy was not fetched
  in the time available. This makes clearance a weak factor; fire risk is driven by gust, humidity and fuel zone.
- **ASOS QC.** Gusts > 45 m/s (one 81.8 m/s reading in a calm week) and gusts below the mean wind are dropped. KPMD is
  outside the bbox and excluded. METARs at HH:53 are assigned to hour HH+1 to match Open-Meteo's hourly convention.
- **Control weeks** were drawn with `random.seed(7)` from Oct-Feb weeks 2019-2024 with no LOX Red Flag Warning and at
  least 14 days from any event (`validation/pick_controls.py`), so false-alarm tests happen in Santa Ana season.
- **Open-Meteo grids.** Events: 0.1 deg (104 points), window padded 2 days before / 1 after. Multi-year series: 0.25 deg
  (24 points) for 2019-2024 to stay within rate limits.

## Grid model
- **Kept every line >= 66 kV**, giving 867 branches and 675 buses (above the suggested 60-200). The 66 kV SCE network in
  the foothills is where Santa Ana ignitions happen; dropping it would hide the risk. DC power flow with precomputed
  PTDFs is still milliseconds per hour.
- **Voltage classes (modeled):** 66-69 kV ACSR Linnet (75 C), 115-138 kV Hawk (75 C), 220-230 kV Drake (100 C),
  500 kV 3-bundle Drake (100 C); reactance 0.45/0.45/0.40/0.33 ohm/km; span 300/300/400/400 m; attach height 20/25/35/40 m;
  reference sag 2% of span at 25 C; right-of-way half-width to vegetation 7/10/15/25 m. Static ratings from our IEEE 738
  at 40 C, 0.61 m/s, 1000 W/m2.
- **Zones** per tower point: mountain (elev >= 500 m, or >= 300 m with slope >= 10 deg), valley (150-500 m),
  coast (< 60 m and < 4 km from the sea), basin (rest).
- **Plan-to-peak ratings.** The simplified topology concentrates flow near the 32 import buses (up to 475% at median
  load before adjustment). Branches above 90% at the 2019-2021 peak hour get more modeled conductor (rating and a
  `thermal_scale` so the per-conductor current drops too). Uses training years only, so H2 (2022, test) can still stress the grid.
- **Loads.** Tracts go to the nearest <= 230 kV substation (Voronoi). BA by OSM operator tag (LADWP/Burbank/Glendale -> LDWP,
  else SCE); untagged substations inherit the nearest tagged one. LDWP BA demand x population share; SCE sub-BA demand x
  (bbox SCE-area population / 15 M SCE service population = 0.62). Munis like Pasadena are lumped with SCE.
- **Generation.** EIA-860 (2023) operating plants >= 20 MW at their nearest bus; dispatched pro rata to meet 45% of load;
  imports split evenly over boundary buses (>= 200 kV within 1.5 km of the bbox edge), highest-kV import is the island slack.
- **Islands without any source** (one 4-bus island) are dropped; every remaining island has a generator or import.

## Physics
- Our own vectorized IEEE 738 (numpy) is used in the engine; `linerate` is the reference in tests/V1. Radiation uses the
  exact pi*D*sigma form (273.15 K) like `linerate`. Solar heating uses the gridded shortwave radiation x absorptivity x D.
- Clearance: conductor low point (attach - sag cos(theta)) to vegetation top at the downwind ROW edge.
- **Fire risk** = sigmoid((gust-g50)/2) x sigmoid((h50-RH)/5) x (0.5 + 0.5 sigmoid((c50-clearance)/1)) x fuel(zone);
  fuel = mountain 1.0, valley 0.7, coast 0.4, basin 0.35, 0 for cables (fixed priors, not calibrated).
- **Region risk index** = sum over tower points of risk x span km ("risk-weighted line-km").
- V4b uses the coarse series without per-hour power flow (conductor temperature approximated as air + 10 C).

## Calibration
- **Gust correction acceptance rule:** a zone's linear map is kept only if it lowers training RMSE on windy station hours.
  None did, so the identity is used. (An all-hours fit made windy-hour bias -5.9 m/s.)
- **Fire thresholds:** grid search g50 6-20 m/s, h50 10-40%, c50 5-13 m. Alarm = 98th percentile of training control-week
  RI (<= 2% false-alarm hours by construction). Objective: hit rate on training wind-event outage-spike hours; tie-break
  AUC (spike hours vs control hours), then peak/alarm margin. The optimum is at the low edge of g50 and h50 and the
  objective is flat there; we report this instead of widening the search until it moved.
- **Line alarm** = 99.5th percentile of line-hour risk in training control weeks, floor 0.3.
- **Load model:** OLS on 2019-2021, features: (T-18)+, (T-26)+, (14-T)+, 24 h mean (T24-20)+, (T24-26)+, hour-of-day and
  hour x weekend dummies; T = population-weighted 2 m temperature from the series grid.

## Validation definitions
- **Outage spike (post-hoc, disclosed):** first of >= 3 consecutive hours with LA County customers out above 2x the
  median of the 2 days before the event. The pre-registered single-hour definition fired on overnight noise; both lead
  times are reported.
- **V4b look-back window** (rolling max over k hours) is chosen on 2019-2021 for the index and the baseline separately.
- **V5** ranks overhead lines only; percentile = rank / N (ties averaged), 0% = riskiest.

## Co-pilot safety policy on the LA grid (changed after first demo, does not affect calibration or V1-V7)
- First version: every line above the fire alarm was a violation, and plans were capped at 3 actions. In Santa Ana
  hours 25-42 lines (median) exceed the alarm and about 60% are 220-500 kV import corridors, so almost no hour had an
  approvable plan. Utilities don't work that way: SCE's Jan 2025 PSPS cut 373 distribution circuits and kept the bulk
  network on.
- Now: sub-transmission lines (< 180 kV) above the alarm are violations; bulk 220-500 kV lines above it are
  patrol-and-monitor items (listed on the plan and in the bulletin). Overloads, conductor over max temperature,
  clearance < 3 m, and blackouts (> 10% of regional load unserved) remain violations.
- New plan actions: one `deenergize_lines` group for all flagged sub-transmission lines; when a shutoff creates a new
  overload, the planner sheds load at the bus with the highest power-flow sensitivity (substations hosting a
  hospital/water plant can rotate up to 50% of their load, never the critical feeder) or redispatches an import point
  (`dispatch_gen` on I1..In; the island slack absorbs the change). A critical site counts as on backup only if its
  substation loses supply.
- Result over every flagged hour of E1/E2/E3 (102 hours): 100 have an approvable plan, 2 do not (overloads the
  3-action limit cannot fix); measured during development, not part of the validation report.

## Product
- The LA replay runs alongside the Phase-1 synthetic "what-if" scenarios (kept as-is). The LA view uses a 3x vertical exaggeration.
- Project renamed Highwire (folder `highwire/`).

## v2 improvements (wind agent, demand model, Gemini)
- **Why V4b failed before.** The fire index answers "will a line ignite?" (needs dry air); V4b's positives are the worst outage
  hours from any cause, mostly wind and wet-storm damage. We kept the fire index (it is right for PSPS/ignition, V5/V7) and
  added a separate **storm-damage model** (`backend/la/storm.py`) for the outage question.
- **Storm model form (physics first, 4 fitted numbers).** Per tower span: damage chance = sigmoid((gust - onset)/2) x
  vegetation-clearance factor; network hazard = span-weighted share; outages follow a **damage stock** that decays with a
  restoration time constant tau (customers stay out until crews fix the line); a rain stock is added only if it helps.
  Logistic weights map the two stocks to "chance this is a top-1% outage hour".
- **What was searched, and where.** Design exploration (~60 variants: wind-only exposure, vegetation, 24/72 h wetness,
  rain, heat, combinations, with/without damage stock) used 2019-2021 only, scored leave-one-year-out. Heat terms hurt and were
  dropped. The final protocol in `calibrate.py fit_storm` searches onset {10..18} x tau {6,12,24,48} then rain tau {6,12,24}.
  2022-2024 and the test events were not looked at until the choice was locked. The gust-only baseline gets the same
  look-back window selection as before.
- **New data.** Open-Meteo hourly precipitation on the 0.25 deg series grid (2019-2024) and Open-Meteo point series at the 9
  ASOS stations (2018-2025). Both are keyless, cached in data/raw, processed into data/processed/weather/.
- **Gust correction, second attempt.** Quantile mapping fit on 2019-2021 station climatology (far more Santa Ana hours than the
  training events) improved all-hours error but made windy test hours worse (RMSE 5.9 -> 8.0): rejected. Live **station
  nudging** (spread other stations' current residuals with weight exp(-d/L)) is evaluated leave-one-station-out; L and
  shrinkage are fit on training events. It is reported in V2; it is NOT fed into the replays (the fire calibration was fit
  on un-nudged gusts and refitting it would need new training data).
- **Demand model.** Added temperature x time-of-day interactions and an inland-heat term; chosen on an internal
  2019-20 fit / summer-2021 holdout (SCE top-5 peak error -13.1% -> -4.8%) before H2 was evaluated.
- **LLM provider.** Switched from Anthropic to Google Gemini (`langchain-google-genai`), single factory in `backend/llm.py`.
  Model id is configurable (`GEMINI_MODEL`); the LLM never produces a number shown in the UI.
