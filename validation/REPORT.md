# Highwire: historical validation report (Greater Los Angeles)

_Generated 2026-11-14 13:35 UTC by `validation/run_all.py`. Every number below is read from `validation/results/*.json`._

**Split (written before any run, `validation/config.yaml`):** train E2, H1, C1, C2; test E1, E3, H2, C3. Multi-year series: calibrate 2019-2021, report 2022-2024.

**Modeled, not utility data:** line ratings, impedances, conductors, tower heights and load allocation are estimated from voltage class and 2020 Census population (see `data/processed/grid_report.md`).

## Scorecard

| Check | Result | Baseline | Status |
|---|---|---|---|
| V1 Thermal model vs reference | Max abs. error 0.00 C over 600 cases | linerate IEEE 738; CIGRE TB 601 Ex. B ampacity 1505 A vs published 1504 A | pass |
| V2 Weather inputs vs ground stations | Test windy-hour gust bias -3.7 m/s (RMSE 5.9) | All test hours: bias +2.1 m/s; temperature RMSE 2.1 C | weak |
| V3 Demand model vs actual (H2 heat wave) | Peak within 10.4% (SCE) / 8.0% (LDWP); MAPE 9.3% / 7.2% | Same hour last week: MAPE 13.8% / 19.3%, peak error 2.7% / 5.2% | mixed |
| V4a Grid stress vs actual outages (event replay) | E1 Spearman 0.13, lead -10 h; E3 Spearman 0.78, lead -49 h | Calm test week C3: alarm on 10.7% of hours | mixed |
| V4b Discrimination over 2022-2024 | Risk index AUC 0.62 | Max gust alone AUC 0.82 | fail |
| V5 Ignition hindcast | Woolsey Fire top 1%; Eaton Fire top 10%; Hurst Fire top 5% | Gust-only: Woolsey Fire top 4%; Eaton Fire top 1%; Hurst Fire top 29%; random: top 50% | small-sample |
| V6 De-energization vs SCE PSPS (E3, county level) | County overlap 3/4 (Jaccard 0.75) | No meaningful baseline at county level | weak |
| V7 Red Flag consistency | 95% of high-risk line-hours inside a Red Flag Warning | 28% of Red Flag zone-hours contain a high-risk line | pass |

## Calibration (training data only)

- **Gust correction:** candidate per-zone linear maps were fit on windy training hours, but none lowered training windy-hour RMSE (identity 3.69 m/s vs candidate 5.88 m/s in the valley), so no correction is applied: a = {'coast': 0.0, 'basin': 0.0, 'valley': 0.0, 'mountain': 0.0}, b = {'coast': 1.0, 'basin': 1.0, 'valley': 1.0, 'mountain': 1.0}.
- **Fire thresholds:** g50 = 6 m/s, h50 = 10% RH, c50 = 5 m; region alarm = 268 risk-weighted line-km (98th pct of training control weeks); line alarm = 0.30. Training hit rate 93% on 43 outage-spike hours, AUC 0.94. The optimum sits at the low edge of the search grid for g50 and h50 and the objective is flat there: on training data, very dry air (RH under ~10%) carries most of the signal.
- **Load model:** training MAPE LDWP 6.5%, SCE 6.8% (2019-2021).

## V1. Thermal model vs reference implementation

**What we tested.** Our numpy IEEE 738 steady-state solver against Statnett's `linerate` (IEEE738 model), plus the published CIGRE TB 601 Example B and IEEE 738 Annex B values.

**Data.** [linerate (Statnett)](https://github.com/statnett/linerate); CIGRE TB 601 Annex E; IEEE 738 Annex B.

**Result.**

- Sweep: 600 cases (current 100-1300 A, wind 0.2-10 m/s, ambient -5..45 C, altitude 0-1800 m, 30/90 deg): max |difference| 0.000 C (tolerance 2 C) -> PASS.
- CIGRE TB 601 Example B via linerate: ampacity 1505 A vs published 1504 A (0.07%); convective 172.2 vs 172.1 W/m; radiative 54.1 vs 54 W/m.
- Our IEEE 738 on the same Example B inputs: 1515 A (different standard; informational).
- IEEE 738 Annex B convection at 100.7 C: ours 83.01 W/m vs published 83.06 W/m.

**Baseline.** The reference implementation and the published worked examples.

![V1](results/v1_thermal.png)

**What it does and doesn't prove.** Our heat-balance code matches the reference library and the published worked examples. That shows correct implementation, not real-world accuracy.

## V2. Weather inputs vs ground stations

**What we tested.** Hourly Open-Meteo values bilinearly interpolated to 8 ASOS stations in the bbox (KPMD is outside and excluded), per event.

**Data.** [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api), [IEM ASOS](https://mesonet.agron.iastate.edu/request/download.phtml). Station gust = max(reported gust, sustained wind) within the hour; gusts above 45 m/s dropped as QC.

**Result.**

- Test events, all hours: gust bias +2.06 m/s, RMSE 3.43 m/s (n=6869); temperature RMSE 2.08 C; sustained wind RMSE 2.00 m/s.
- Test events, windy hours (station gust >= 12 m/s, n=280): bias -3.67 m/s, RMSE 5.87 m/s. After correction: identical (no correction was accepted in training).
- E1 (test): peak station gust 15.9 m/s vs peak gridded gust 22.2 m/s.
- E2 (train): peak station gust 14.9 m/s vs peak gridded gust 16.0 m/s.
- E3 (test): peak station gust 37.0 m/s vs peak gridded gust 26.1 m/s.
- H1 (train): peak station gust 9.8 m/s vs peak gridded gust 14.4 m/s.
- H2 (test): peak station gust 19.5 m/s vs peak gridded gust 17.1 m/s.
- C1 (train): peak station gust 21.1 m/s vs peak gridded gust 23.0 m/s.
- C2 (train): peak station gust 11.3 m/s vs peak gridded gust 10.2 m/s.
- C3 (test): peak station gust 16.5 m/s vs peak gridded gust 11.9 m/s.

**Baseline.** Raw reanalysis before correction.

![V2](results/v2_weather.png)

**What it does and doesn't prove.** Reanalysis gusts are close on ordinary hours but miss the strongest Santa Ana gusts; the training events did not contain enough of them to correct this.

## V3. Demand model vs actual load (H2, test)

**What we tested.** Hourly demand predicted from population-weighted temperature (piecewise linear cooling/heating terms, 24 h mean) plus hour-of-day x weekend terms, fit 2019-2021, applied to Aug 31 - Sep 9 2022.

**Data.** [EIA-930](https://www.eia.gov/electricity/gridmonitor/about) (LDWP BA, CAISO SCE sub-BA), [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api).

**Result.**

- SCE: peak 22038 MW predicted vs 24595 MW actual (-10.4%), peak-hour error -167 h, MAPE 9.3%.
- LDWP: peak 5677 vs 6170 MW (-8.0%), peak-hour error -48 h, MAPE 7.2%.

**Baseline.** Same hour last week: SCE MAPE 13.8% / peak -2.7%; LDWP MAPE 19.3% / peak -5.2%.

![V3](results/v3_demand.png)

**What it does and doesn't prove.** Weather-only demand tracks the heat wave better hour by hour than persistence, but underpredicts the record peak by more than persistence does.

## V4a. Grid stress vs actual outages (event replay)

**What we tested.** Hourly region risk index (sum over tower points of fire risk x span km) from a full hindcast (power flow, IEEE 738, sag, blowout, clearance) vs LA County customers out. Lead time = first hour the index crosses its calibrated alarm to the first of 3 consecutive hours with outages above 2x the 2-day pre-event median (positive = the model was early).

**Data.** [EAGLE-I outages 2014-2025 (figshare)](https://figshare.com/articles/dataset/24237376) (hourly median of 15-min runs; LA County readings > 1 M dropped as QC), [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api), [EIA-930](https://www.eia.gov/electricity/gridmonitor/about).

**Result.**

- E1 (test, wind): Spearman 0.13 (4 counties 0.13); first alarm 2018-11-08 18:00:00+00:00; first sustained outage spike 2018-11-08 08:00:00+00:00; lead -10 h (single-hour spike definition: -10 h); alarm on 77.5% of hours; LA County peak 27534 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2018-11-09 14:00:00+00:00) 20 h._
- E2 (train, wind): Spearman 0.75 (4 counties 0.64); first alarm 2019-10-10 17:00:00+00:00; first sustained outage spike 2019-10-10 18:00:00+00:00; lead 1 h (single-hour spike definition: -10 h); alarm on 68.1% of hours; LA County peak 6896 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2019-10-12 18:00:00+00:00) 49 h._
- E3 (test, wind): Spearman 0.78 (4 counties 0.82); first alarm 2025-01-08 18:00:00+00:00; first sustained outage spike 2025-01-06 17:00:00+00:00; lead -49 h (single-hour spike definition: -49 h); alarm on 25.0% of hours; LA County peak 362313 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2025-01-08 19:00:00+00:00) 1 h._
- C1 (train, control): Spearman 0.35 (4 counties 0.13); first alarm never; first sustained outage spike 2020-01-27 18:00:00+00:00; lead n/a h (single-hour spike definition: n/a h); alarm on 0.0% of hours; LA County peak 4926 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2020-01-27 18:00:00+00:00) n/a h._
- C2 (train, control): Spearman 0.03 (4 counties 0.22); first alarm 2021-11-29 20:00:00+00:00; first sustained outage spike 2021-11-29 17:00:00+00:00; lead -3 h (single-hour spike definition: -3 h); alarm on 4.2% of hours; LA County peak 4516 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2021-11-30 17:00:00+00:00) 21 h._
- C3 (test, control): Spearman 0.25 (4 counties 0.30); first alarm 2022-02-02 18:00:00+00:00; first sustained outage spike 2022-01-31 08:00:00+00:00; lead -58 h (single-hour spike definition: -58 h); alarm on 10.7% of hours; LA County peak 9498 customers out. _Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, 2022-01-31 20:00:00+00:00) -46 h._

**Baseline.** Calm control weeks give the false-alarm side; V4b gives the gust-only baseline.

![V4a](results/v4a_replay.png)

**What it does and doesn't prove.** The risk index co-moves with outages most in E3 (Spearman 0.78). In every test wind event (E1, E3) outages began rising before our alarm, so this is not yet an early warning.

## V4b. Discrimination over years (2022-2024)

**What we tested.** Can the hourly risk index separate the top 1% LA County outage hours (> 13483 customers, 176 of 26304 hours) from the rest? Look-back window chosen on 2019-2021 for both scores.

**Data.** [EAGLE-I outages 2014-2025 (figshare)](https://figshare.com/articles/dataset/24237376), [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api) (0.25 deg grid, 2019-2024).

**Result.**

- Risk index: AUC 0.619 (look-back 24 h; calibration-years AUC 0.627).
- Gust-only baseline: AUC 0.820 (look-back 6 h).
- Beats baseline: NO. Positives include every cause of high outages (heat, rain, equipment); non-weather causes are not separable in EAGLE-I.

**Baseline.** Regional maximum gridded gust.

![V4b](results/v4b_discrimination.png)

**What it does and doesn't prove.** As a general predictor of the worst outage hours, our fire-weather index does NOT beat gust alone: many outage peaks come from wet winter storms that are windy but not fire weather.

## V5. Ignition hindcast

**What we tested.** Rank of the overhead line nearest each utility-related ignition, by model fire risk at the ignition hour.

**Data.** [NIFC WFIGS](https://data-nifc.opendata.arcgis.com) origins and times; [CPUC wildfire investigations & PSPS reports](https://www.cpuc.ca.gov/psps) (SED Woolsey report, SCE electric safety incident reports).

**Result.**

- Woolsey Fire (test, utility-related: confirmed), ignition 2018-11-08 22:00:00+00:00: nearest modeled line L249 (66 kV, 0.41 km from origin) ranked top 1.3% of 829 overhead lines (risk 0.33); gust-only rank top 4.0%. Caveat: the igniting facility is a 16 kV distribution circuit, below the 66 kV model floor. [source](https://www.cpuc.ca.gov/-/media/cpuc-website/divisions/safety-and-enforcement-division/investigations-wildfires/sed-investigation-report---woolsey-fire-redacted.pdf)
- Saddleridge Fire (train, utility-related: suspected), ignition 2019-10-11 03:00:00+00:00: nearest modeled line L171 (220 kV, 0.26 km from origin) ranked top 0.7% of 829 overhead lines (risk 0.38); gust-only rank top 5.3%.  [source](https://abc7.com/sylmar-fire-in-saddleridge-freeway-210/5619488/)
- Eaton Fire (test, utility-related: confirmed), ignition 2025-01-08 02:00:00+00:00: nearest modeled line L29 (220 kV, 0.28 km from origin) ranked top 10.4% of 829 overhead lines (risk 0.08); gust-only rank top 0.7%. Caveat: the igniting tower carried an idle (de-energized) line; OSM may not map it. [source](https://www.cpuc.ca.gov/-/media/cpuc-website/divisions/safety-and-enforcement-division/electric-safety-incidents-reported-by-ious/2025-incidents/eaton-fire-update-1-27-25.pdf)
- Hurst Fire (test, utility-related: suspected), ignition 2025-01-08 06:00:00+00:00: nearest modeled line L172 (230 kV, 0.52 km from origin) ranked top 5.3% of 829 overhead lines (risk 0.06); gust-only rank top 29.1%.  [source](https://www.cpuc.ca.gov/-/media/cpuc-website/divisions/safety-and-enforcement-division/electric-safety-incidents-reported-by-ious/2025-incidents/2025-1-10-eir.pdf)
- Test median: model top 5.3%, gust-only top 4.0%, random 50%.

**Baseline.** Random rank (50%) and ranking by gust alone.

**What it does and doesn't prove.** The line nearest each test ignition ranked in the top 10% of 829 lines at ignition time; 3 cases is far too few for a rate.

## V6. De-energization comparison (E3, stretch)

**What we tested.** Counties where our rule (de-energize any overhead line reaching the line alarm) would act during Jan 6-10 2025 vs counties SCE de-energized.

**Data.** [CPUC wildfire investigations & PSPS reports](https://www.cpuc.ca.gov/psps): SCE PSPS post-event report for the Jan 2-17 2025 event (363,196 customers, 373 circuits).

**Result.**

- Ours: Los Angeles, Orange, Ventura (38 lines, 1445 km). SCE (in bbox): Los Angeles, Orange, San Bernardino, Ventura.
- Overlap Los Angeles, Orange, Ventura; Jaccard 0.75.
- First flag (local): Los Angeles 2025-01-08 13:00:00-08:00, Ventura 2025-01-08 13:00:00-08:00, Orange 2025-01-09 11:00:00-08:00.

**Baseline.** None meaningful at county level (SCE de-energized circuits in every bbox county).

**What it does and doesn't prove.** Our de-energization counties match most of SCE's at county level, which is too coarse to show we would have chosen the same circuits. County-level only; SCE's per-circuit table (Attachment C) was not reachable, so this cannot test where or when within a county.

## V7. Red Flag consistency

**What we tested.** Overlap between our high-risk line-hours and NWS Red Flag Warnings (zone polygons as issued) over all event windows.

**Data.** [IEM NWS VTEC archive](https://mesonet.agron.iastate.edu/vtec/) (LOX FW.W, zone geometry per event).

**Result.**

- 95.0% of high-risk line-hours fall inside an active Red Flag zone.
- 28.1% of Red Flag zone-hours (zones crossed by our lines) contain at least one high-risk line.
- Per event: E1 2080/2201; E2 1843/1916; E3 179/189; C2 0/2; C3 0/8

**Baseline.** NWS Red Flag Warnings.

**What it does and doesn't prove.** When we flag a line, NWS forecasters had almost always flagged that area too; we flag far less than they do (conservative).

## Limitations

- **Modeled electrical values.** Conductors, ratings, impedances, tower heights, sag and right-of-way widths come from voltage class, not utility records. Ratings on 252 of 864 branches were raised so the simplified network carries the 2019-2021 peak (see grid_report.md).
- **Simplified topology.** OSM lines >= 66 kV snapped to OSM substations; no transformers, no distribution (where many ignitions start, e.g. Woolsey's 16 kV circuit), imports split evenly over boundary buses, in-basin plants dispatched pro rata to 45% of load.
- **Reanalysis gust bias.** V2 shows the gridded gusts miss the strongest Santa Ana gusts in the test events; training events did not contain enough extreme gusts to correct it, which delays our alarms in E3.
- **Vegetation.** Canopy is a constant per terrain zone (NLCD not fetched), so clearance contributes little; fire risk is mostly gust x dryness x fuel zone.
- **Small ignition sample.** V5 has 3 test ignitions and 1 training ignition. Report individual cases only.
- **EAGLE-I gaps.** Missing 15-min runs stay missing; implausible LA County readings (> 1 M customers, Jan 8 2025) were dropped; non-weather outage causes cannot be separated.
- **Definition changes disclosed.** The outage-spike definition was changed after the first run from 1 hour to 3 consecutive hours above 2x baseline; both lead times are reported. The 'lead to main surge' metric was added after seeing the plots and is labeled exploratory; it is not used in the scorecard.
- **Retrospective only.** Hindcasts use reanalysis weather, not the forecasts an operator would have had at decision time.

_Run time: V1 2s, V2 4s, V3 0s, V4a 80s, V4b 127s, V5 14s, V6 6s, V7 57s._
