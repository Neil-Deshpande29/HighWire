"""Run every validation check, then compile validation/REPORT.md and validation/results/summary.json.
Every number in the report is read back from validation/results/*.json (never typed by hand)."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from checks import v1_thermal, v2_weather, v3_demand, v4a_replay, v4b_discrimination, v5_ignition, v6_psps, v7_redflag  # noqa: E402
from common import D, RES  # noqa: E402

CHECKS = [("V1", v1_thermal), ("V2", v2_weather), ("V3", v3_demand), ("V4a", v4a_replay), ("V4b", v4b_discrimination),
          ("V5", v5_ignition), ("V6", v6_psps), ("V7", v7_redflag)]

SRC = {
    "openmeteo": "[Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api)",
    "asos": "[IEM ASOS](https://mesonet.agron.iastate.edu/request/download.phtml)",
    "eia930": "[EIA-930](https://www.eia.gov/electricity/gridmonitor/about)",
    "eaglei": "[EAGLE-I outages 2014-2025 (figshare)](https://figshare.com/articles/dataset/24237376)",
    "linerate": "[linerate (Statnett)](https://github.com/statnett/linerate)",
    "osm": "[OpenStreetMap / Overpass](https://overpass-api.de)",
    "wfigs": "[NIFC WFIGS](https://data-nifc.opendata.arcgis.com)",
    "cpuc": "[CPUC wildfire investigations & PSPS reports](https://www.cpuc.ca.gov/psps)",
    "iem_vtec": "[IEM NWS VTEC archive](https://mesonet.agron.iastate.edu/vtec/)",
}


def r(name):
    return json.loads((RES / f"{name}.json").read_text())


def fmt(x, nd=1):
    return "n/a" if x is None else f"{x:.{nd}f}"


def v4a_sentence(v4a):
    test = {k: v for k, v in v4a["events"].items() if v["split"] == "test" and v["kind"] == "wind"}
    leads = {k: v["lead_time_h"] for k, v in test.items()}
    early = [k for k, x in leads.items() if x is not None and x > 0]
    late = [k for k, x in leads.items() if x is not None and x <= 0]
    best = max(test.items(), key=lambda kv: kv[1]["spearman_la"])
    s = f"The risk index co-moves with outages most in {best[0]} (Spearman {best[1]['spearman_la']:.2f}). "
    if late and not early:
        s += f"In every test wind event ({', '.join(late)}) outages began rising before our alarm, so this is not yet an early warning."
    elif early and not late:
        s += f"The alarm led the outage spike in every test wind event ({', '.join(early)})."
    else:
        s += f"The alarm led in {', '.join(early) or 'none'} and lagged in {', '.join(late) or 'none'}."
    return s


def summary_cards():
    v1, v2, v3, v4a, v4b, v5, v6, v7 = (r(n) for n in ("v1_thermal", "v2_weather", "v3_demand", "v4a_replay", "v4b_discrimination",
                                                       "v5_ignition", "v6_psps", "v7_redflag"))
    e1, e3, c3 = v4a["events"]["E1"], v4a["events"]["E3"], v4a["events"]["C3"]
    sce, ldwp = v3["by_ba"]["SCE"], v3["by_ba"]["LDWP"]
    test5 = [c for c in v5["cases"] if c["split"] == "test"]
    ts = v2["test_summary"]
    cards = [
        {"id": "V1", "title": "Thermal model vs reference", "status": "pass" if v1["pass"] else "fail",
         "headline": f"Max abs. error {v1['c_sweep']['max_abs_err_c']:.2f} C over {v1['c_sweep']['n_cases']} cases",
         "baseline": f"linerate IEEE 738; CIGRE TB 601 Ex. B ampacity {v1['a_cigre601_example_b']['linerate']['ampacity_a']:.0f} A vs published 1504 A",
         "plot": "v1_thermal.png",
         "one_liner": "Our heat-balance code matches the reference library and the published worked examples. That shows correct implementation, not real-world accuracy."},
        {"id": "V2", "title": "Weather inputs vs ground stations", "status": "weak",
         "headline": f"Test windy-hour gust bias {ts['gust_windy_before']['bias']:+.1f} m/s (RMSE {ts['gust_windy_before']['rmse']:.1f})",
         "baseline": f"All test hours: bias {ts['gust_all_before']['bias']:+.1f} m/s; temperature RMSE {ts['temp']['rmse']:.1f} C",
         "plot": "v2_weather.png",
         "one_liner": "Reanalysis gusts are close on ordinary hours but miss the strongest Santa Ana gusts; the training events did not contain enough of them to correct this."},
        {"id": "V3", "title": "Demand model vs actual (H2 heat wave)", "status": "mixed",
         "headline": f"Peak within {abs(sce['model']['peak_error_pct']):.1f}% (SCE) / {abs(ldwp['model']['peak_error_pct']):.1f}% (LDWP); MAPE {sce['model']['mape_pct']:.1f}% / {ldwp['model']['mape_pct']:.1f}%",
         "baseline": f"Same hour last week: MAPE {sce['baseline_last_week']['mape_pct']:.1f}% / {ldwp['baseline_last_week']['mape_pct']:.1f}%, peak error {abs(sce['baseline_last_week']['peak_error_pct']):.1f}% / {abs(ldwp['baseline_last_week']['peak_error_pct']):.1f}%",
         "plot": "v3_demand.png",
         "one_liner": "Weather-only demand tracks the heat wave better hour by hour than persistence, but underpredicts the record peak by more than persistence does."},
        {"id": "V4a", "title": "Grid stress vs actual outages (event replay)", "status": "mixed",
         "headline": f"E1 Spearman {e1['spearman_la']:.2f}, lead {fmt(e1['lead_time_h'], 0)} h; E3 Spearman {e3['spearman_la']:.2f}, lead {fmt(e3['lead_time_h'], 0)} h",
         "baseline": f"Calm test week C3: alarm on {c3['alarm_hour_fraction'] * 100:.1f}% of hours",
         "plot": "v4a_replay.png",
         "one_liner": v4a_sentence(v4a)},
        {"id": "V4b", "title": "Discrimination over 2022-2024", "status": "fail" if not v4b["beats_baseline"] else "pass",
         "headline": f"Risk index AUC {v4b['features']['risk_index']['auc_report']:.2f}",
         "baseline": f"Max gust alone AUC {v4b['features']['baseline_max_gust']['auc_report']:.2f}",
         "plot": "v4b_discrimination.png",
         "one_liner": "As a general predictor of the worst outage hours, our fire-weather index does NOT beat gust alone: many outage peaks come from wet winter storms that are windy but not fire weather."},
        {"id": "V5", "title": "Ignition hindcast", "status": "small-sample",
         "headline": "; ".join(f"{c['fire']} top {c['model_top_pct']:.0f}%" for c in test5),
         "baseline": "Gust-only: " + "; ".join(f"{c['fire']} top {c['gust_only_top_pct']:.0f}%" for c in test5) + "; random: top 50%",
         "plot": None,
         "one_liner": f"The line nearest each test ignition ranked in the top {max(c['model_top_pct'] for c in test5):.0f}% of {test5[0]['n_lines']} lines at ignition time; {len(test5)} cases is far too few for a rate."},
        {"id": "V6", "title": "De-energization vs SCE PSPS (E3, county level)", "status": "weak",
         "headline": f"County overlap {len(v6['overlap'])}/{len(set(v6['our_counties']) | set(v6['sce_counties_in_bbox']))} (Jaccard {v6['jaccard']:.2f})",
         "baseline": "No meaningful baseline at county level", "plot": None,
         "one_liner": "Our de-energization counties match most of SCE's at county level, which is too coarse to show we would have chosen the same circuits."},
        {"id": "V7", "title": "Red Flag consistency", "status": "pass" if (v7["frac_high_risk_inside_red_flag"] or 0) > 0.8 else "mixed",
         "headline": f"{v7['frac_high_risk_inside_red_flag'] * 100:.0f}% of high-risk line-hours inside a Red Flag Warning",
         "baseline": f"{v7['frac_red_flag_zone_hours_with_high_risk'] * 100:.0f}% of Red Flag zone-hours contain a high-risk line",
         "plot": None,
         "one_liner": "When we flag a line, NWS forecasters had almost always flagged that area too; we flag far less than they do (conservative)."},
    ]
    return cards


def write_report(cards, timings):
    cal = json.loads((D.P / "calibration.json").read_text())
    cfg = D.config()
    v1, v2, v3, v4a, v4b, v5, v6, v7 = (r(n) for n in ("v1_thermal", "v2_weather", "v3_demand", "v4a_replay", "v4b_discrimination",
                                                       "v5_ignition", "v6_psps", "v7_redflag"))
    calsum = r("calibration_summary")
    L = []
    w = L.append
    w("# Highwire: historical validation report (Greater Los Angeles)")
    w("")
    w(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `validation/run_all.py`. Every number below is read from `validation/results/*.json`._")
    w("")
    w(f"**Split (written before any run, `validation/config.yaml`):** train {', '.join(cfg['split']['train'])}; test {', '.join(cfg['split']['test'])}. "
      f"Multi-year series: calibrate {cfg['series']['calibrate_years'][0]}-{cfg['series']['calibrate_years'][-1]}, report {cfg['series']['report_years'][0]}-{cfg['series']['report_years'][-1]}.")
    w("")
    w("**Modeled, not utility data:** line ratings, impedances, conductors, tower heights and load allocation are estimated from voltage class and "
      "2020 Census population (see `data/processed/grid_report.md`).")
    w("")
    w("## Scorecard")
    w("")
    w("| Check | Result | Baseline | Status |")
    w("|---|---|---|---|")
    for c in cards:
        w(f"| {c['id']} {c['title']} | {c['headline']} | {c['baseline']} | {c['status']} |")
    w("")
    w("## Calibration (training data only)")
    w("")
    g = calsum["gust"]
    w(f"- **Gust correction:** candidate per-zone linear maps were fit on windy training hours, but none lowered training windy-hour RMSE "
      f"(identity {g['zones']['valley']['windy_rmse_identity']:.2f} m/s vs candidate {g['zones']['valley']['windy_rmse_candidate']:.2f} m/s in the valley), "
      f"so no correction is applied: a = {cal['gust']['a']}, b = {cal['gust']['b']}.")
    f = calsum["fire"]
    w(f"- **Fire thresholds:** g50 = {cal['fire']['g50']} m/s, h50 = {cal['fire']['h50']}% RH, c50 = {cal['fire']['c50']} m; region alarm = "
      f"{cal['fire']['region_alarm']:.0f} risk-weighted line-km (98th pct of training control weeks); line alarm = {cal['fire']['line_alarm']:.2f}. "
      f"Training hit rate {f['best_hit_rate'] * 100:.0f}% on {sum(f['spike_hours'].values())} outage-spike hours, AUC {f['best_auc']:.2f}. "
      "The optimum sits at the low edge of the search grid for g50 and h50 and the objective is flat there: on training data, very dry air "
      "(RH under ~10%) carries most of the signal.")
    w(f"- **Load model:** training MAPE LDWP {calsum['load']['LDWP']['train_mape']:.1f}%, SCE {calsum['load']['SCE']['train_mape']:.1f}% (2019-2021).")
    w("")

    def section(cid, title, tested, data, result_lines, baseline, plot, sentence):
        w(f"## {cid}. {title}")
        w("")
        w(f"**What we tested.** {tested}")
        w("")
        w(f"**Data.** {data}")
        w("")
        w("**Result.**")
        w("")
        for x in result_lines:
            w(f"- {x}")
        w("")
        w(f"**Baseline.** {baseline}")
        w("")
        if plot:
            w(f"![{cid}](results/{plot})")
            w("")
        w(f"**What it does and doesn't prove.** {sentence}")
        w("")

    a = v1["a_cigre601_example_b"]
    section("V1", "Thermal model vs reference implementation",
            "Our numpy IEEE 738 steady-state solver against Statnett's `linerate` (IEEE738 model), plus the published CIGRE TB 601 Example B and IEEE 738 Annex B values.",
            f"{SRC['linerate']}; CIGRE TB 601 Annex E; IEEE 738 Annex B.",
            [f"Sweep: {v1['c_sweep']['n_cases']} cases (current 100-1300 A, wind 0.2-10 m/s, ambient -5..45 C, altitude 0-1800 m, 30/90 deg): "
             f"max |difference| {v1['c_sweep']['max_abs_err_c']:.3f} C (tolerance 2 C) -> {'PASS' if v1['c_sweep']['pass'] else 'FAIL'}.",
             f"CIGRE TB 601 Example B via linerate: ampacity {a['linerate']['ampacity_a']:.0f} A vs published 1504 A ({a['ampacity_error_pct']:.2f}%); "
             f"convective {a['linerate']['convective_w_m']:.1f} vs 172.1 W/m; radiative {a['linerate']['radiative_w_m']:.1f} vs 54 W/m.",
             f"Our IEEE 738 on the same Example B inputs: {a['highwire_ieee738_ampacity_same_inputs_a']:.0f} A (different standard; informational).",
             f"IEEE 738 Annex B convection at 100.7 C: ours {v1['b_ieee738_annex_b']['highwire']:.2f} W/m vs published 83.06 W/m."],
            "The reference implementation and the published worked examples.", v1["plot"], cards[0]["one_liner"])

    ts = v2["test_summary"]
    lines = [f"Test events, all hours: gust bias {ts['gust_all_before']['bias']:+.2f} m/s, RMSE {ts['gust_all_before']['rmse']:.2f} m/s "
             f"(n={ts['gust_all_before']['n']}); temperature RMSE {ts['temp']['rmse']:.2f} C; sustained wind RMSE {ts['wind']['rmse']:.2f} m/s.",
             f"Test events, windy hours (station gust >= 12 m/s, n={ts['gust_windy_before']['n']}): bias {ts['gust_windy_before']['bias']:+.2f} m/s, "
             f"RMSE {ts['gust_windy_before']['rmse']:.2f} m/s. After correction: identical (no correction was accepted in training)."]
    for eid, ev in v2["events"].items():
        lines.append(f"{eid} ({ev['split']}): peak station gust {ev['all']['gust_peak_obs']:.1f} m/s vs peak gridded gust {ev['all']['gust_peak_grid']:.1f} m/s.")
    section("V2", "Weather inputs vs ground stations",
            "Hourly Open-Meteo values bilinearly interpolated to 8 ASOS stations in the bbox (KPMD is outside and excluded), per event.",
            f"{SRC['openmeteo']}, {SRC['asos']}. Station gust = max(reported gust, sustained wind) within the hour; gusts above 45 m/s dropped as QC.",
            lines, "Raw reanalysis before correction.", v2["plot"], cards[1]["one_liner"])

    s, l = v3["by_ba"]["SCE"], v3["by_ba"]["LDWP"]
    section("V3", "Demand model vs actual load (H2, test)",
            "Hourly demand predicted from population-weighted temperature (piecewise linear cooling/heating terms, 24 h mean) plus hour-of-day x weekend terms, fit 2019-2021, applied to Aug 31 - Sep 9 2022.",
            f"{SRC['eia930']} (LDWP BA, CAISO SCE sub-BA), {SRC['openmeteo']}.",
            [f"SCE: peak {s['model']['peak_pred_mw']:.0f} MW predicted vs {s['model']['peak_actual_mw']:.0f} MW actual ({s['model']['peak_error_pct']:+.1f}%), "
             f"peak-hour error {s['model']['peak_hour_error_h']:+.0f} h, MAPE {s['model']['mape_pct']:.1f}%.",
             f"LDWP: peak {l['model']['peak_pred_mw']:.0f} vs {l['model']['peak_actual_mw']:.0f} MW ({l['model']['peak_error_pct']:+.1f}%), "
             f"peak-hour error {l['model']['peak_hour_error_h']:+.0f} h, MAPE {l['model']['mape_pct']:.1f}%."],
            f"Same hour last week: SCE MAPE {s['baseline_last_week']['mape_pct']:.1f}% / peak {s['baseline_last_week']['peak_error_pct']:+.1f}%; "
            f"LDWP MAPE {l['baseline_last_week']['mape_pct']:.1f}% / peak {l['baseline_last_week']['peak_error_pct']:+.1f}%.",
            v3["plot"], cards[2]["one_liner"])

    lines = []
    for eid, e in v4a["events"].items():
        lines.append(f"{eid} ({e['split']}, {e['kind']}): Spearman {e['spearman_la']:.2f} (4 counties {e['spearman_4county']:.2f}); "
                     f"first alarm {e['first_alarm_utc'] or 'never'}; first sustained outage spike {e['first_spike_utc'] or 'none'}; "
                     f"lead {fmt(e['lead_time_h'], 0)} h (single-hour spike definition: {fmt(e['lead_time_h_single_hour_definition'], 0)} h); "
                     f"alarm on {e['alarm_hour_fraction'] * 100:.1f}% of hours; LA County peak {e['outage_peak_la']:.0f} customers out. "
                     f"_Exploratory (post-hoc): lead to the main surge (first hour >= 50% of peak, {e['main_surge_utc']}) {fmt(e['exploratory_lead_to_main_surge_h'], 0)} h._")
    section("V4a", "Grid stress vs actual outages (event replay)",
            "Hourly region risk index (sum over tower points of fire risk x span km) from a full hindcast (power flow, IEEE 738, sag, blowout, clearance) vs LA County customers out. "
            "Lead time = first hour the index crosses its calibrated alarm to the first of 3 consecutive hours with outages above 2x the 2-day pre-event median "
            "(positive = the model was early).",
            f"{SRC['eaglei']} (hourly median of 15-min runs; LA County readings > 1 M dropped as QC), {SRC['openmeteo']}, {SRC['eia930']}.",
            lines, "Calm control weeks give the false-alarm side; V4b gives the gust-only baseline.", v4a["plot"], cards[3]["one_liner"])

    fb = v4b["features"]
    section("V4b", "Discrimination over years (2022-2024)",
            f"Can the hourly risk index separate the top 1% LA County outage hours (> {v4b['positive_threshold_customers']:.0f} customers, "
            f"{v4b['n_positive_hours']} of {v4b['n_hours']} hours) from the rest? Look-back window chosen on 2019-2021 for both scores.",
            f"{SRC['eaglei']}, {SRC['openmeteo']} (0.25 deg grid, 2019-2024).",
            [f"Risk index: AUC {fb['risk_index']['auc_report']:.3f} (look-back {fb['risk_index']['k_hours']} h; calibration-years AUC {fb['risk_index']['auc_calibrate']:.3f}).",
             f"Gust-only baseline: AUC {fb['baseline_max_gust']['auc_report']:.3f} (look-back {fb['baseline_max_gust']['k_hours']} h).",
             f"Beats baseline: {'yes' if v4b['beats_baseline'] else 'NO'}. {v4b['caveat']}"],
            "Regional maximum gridded gust.", v4b["plot"], cards[4]["one_liner"])

    lines = []
    for c in v5["cases"]:
        lines.append(f"{c['fire']} ({c['split']}, utility-related: {c['utility_related']}), ignition {c['ignition_utc']}: nearest modeled line {c['nearest_line']} "
                     f"({c['nearest_line_kv']:.0f} kV, {c['distance_km']:.2f} km from origin) ranked top {c['model_top_pct']:.1f}% of {c['n_lines']} overhead lines "
                     f"(risk {c['line_risk']:.2f}); gust-only rank top {c['gust_only_top_pct']:.1f}%. {('Caveat: ' + c['caveat'] + '.') if c['caveat'] else ''} "
                     f"[source]({c['source_url']})")
    section("V5", "Ignition hindcast",
            "Rank of the overhead line nearest each utility-related ignition, by model fire risk at the ignition hour.",
            f"{SRC['wfigs']} origins and times; {SRC['cpuc']} (SED Woolsey report, SCE electric safety incident reports).",
            lines + [f"Test median: model top {fmt(v5['median_model_top_pct_test'])}%, gust-only top {fmt(v5['median_gust_top_pct_test'])}%, random 50%."],
            "Random rank (50%) and ranking by gust alone.", None, cards[5]["one_liner"])

    section("V6", "De-energization comparison (E3, stretch)",
            "Counties where our rule (de-energize any overhead line reaching the line alarm) would act during Jan 6-10 2025 vs counties SCE de-energized.",
            f"{SRC['cpuc']}: SCE PSPS post-event report for the Jan 2-17 2025 event ({v6['sce_customers_deenergized']:,} customers, {v6['sce_circuits_deenergized']} circuits).",
            [f"Ours: {', '.join(v6['our_counties'])} ({v6['lines_flagged']} lines, {v6['line_km_flagged']:.0f} km). SCE (in bbox): {', '.join(v6['sce_counties_in_bbox'])}.",
             f"Overlap {', '.join(v6['overlap'])}; Jaccard {v6['jaccard']:.2f}.",
             "First flag (local): " + ", ".join(f"{k} {v}" for k, v in v6["first_flag_local"].items()) + "."],
            v6["baseline"], None, cards[6]["one_liner"] + " " + v6["caveat"])

    section("V7", "Red Flag consistency",
            "Overlap between our high-risk line-hours and NWS Red Flag Warnings (zone polygons as issued) over all event windows.",
            f"{SRC['iem_vtec']} (LOX FW.W, zone geometry per event).",
            [f"{v7['frac_high_risk_inside_red_flag'] * 100:.1f}% of high-risk line-hours fall inside an active Red Flag zone.",
             f"{v7['frac_red_flag_zone_hours_with_high_risk'] * 100:.1f}% of Red Flag zone-hours (zones crossed by our lines) contain at least one high-risk line.",
             "Per event: " + "; ".join(f"{k} {v['inside_red_flag']}/{v['high_risk_line_hours']}" for k, v in v7["events"].items() if v["high_risk_line_hours"])],
            "NWS Red Flag Warnings.", None, cards[7]["one_liner"])

    w("## Limitations")
    w("")
    for x in [
        "**Modeled electrical values.** Conductors, ratings, impedances, tower heights, sag and right-of-way widths come from voltage class, not utility records. "
        f"Ratings on {sum('thermal_scale' in b for b in D.grid()['branches'])} of {len(D.grid()['branches'])} branches were raised so the simplified network carries the 2019-2021 peak (see grid_report.md).",
        "**Simplified topology.** OSM lines >= 66 kV snapped to OSM substations; no transformers, no distribution (where many ignitions start, e.g. Woolsey's 16 kV circuit), "
        "imports split evenly over boundary buses, in-basin plants dispatched pro rata to 45% of load.",
        "**Reanalysis gust bias.** V2 shows the gridded gusts miss the strongest Santa Ana gusts in the test events; training events did not contain enough extreme gusts to correct it, "
        "which delays our alarms in E3.",
        "**Vegetation.** Canopy is a constant per terrain zone (NLCD not fetched), so clearance contributes little; fire risk is mostly gust x dryness x fuel zone.",
        "**Small ignition sample.** V5 has 3 test ignitions and 1 training ignition. Report individual cases only.",
        "**EAGLE-I gaps.** Missing 15-min runs stay missing; implausible LA County readings (> 1 M customers, Jan 8 2025) were dropped; non-weather outage causes cannot be separated.",
        "**Definition changes disclosed.** The outage-spike definition was changed after the first run from 1 hour to 3 consecutive hours above 2x baseline; both lead times are reported. "
        "The 'lead to main surge' metric was added after seeing the plots and is labeled exploratory; it is not used in the scorecard.",
        "**Retrospective only.** Hindcasts use reanalysis weather, not the forecasts an operator would have had at decision time.",
    ]:
        w(f"- {x}")
    w("")
    w(f"_Run time: " + ", ".join(f"{k} {v:.0f}s" for k, v in timings.items()) + "._")
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n")


def main():
    timings = {}
    for cid, mod in CHECKS:
        t = time.time()
        print(f"running {cid} ...", flush=True)
        mod.run()
        timings[cid] = time.time() - t
    import build_replays

    build_replays.main()
    cards = summary_cards()
    (RES / "summary.json").write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(), "cards": cards}, indent=1))
    write_report(cards, timings)
    print("wrote validation/REPORT.md")


if __name__ == "__main__":
    main()
