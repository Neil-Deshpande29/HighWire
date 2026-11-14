"""Deterministic grid physics. numpy only. No LLM calls here, ever.

Public entry points:
    simulate(state, actions=None) -> SimResult (a plain dict)
    is_safe(result) -> bool

`state` is either a GridState (anything with .snapshot()) or a snapshot dict:
    {buses, lines, gens, trees, weather, load_multiplier, deenergized, shed}
"""
from __future__ import annotations

import copy
import math

import numpy as np

# Conductor / physical constants (simplified IEEE 738)
D = 0.028  # conductor diameter, m
R25 = 7e-5  # ohm/m at 25 C
ALPHA_R = 0.004  # resistance temp coefficient, 1/C
ALPHA_L = 2.3e-5  # thermal expansion of aluminium, 1/C
SIGMA = 5.67e-8
EMISSIVITY = 0.5
ABSORPTIVITY = 0.5
CONDUCTOR_WEIGHT_N_M = 15.0
CLEARANCE_MIN_M = 3.0
BASE_MVA = 100.0
CUSTOMERS_PER_MW = 500


# --------------------------------------------------------------------------- actions
def apply_actions(net: dict, actions: list | None) -> list[str]:
    """Mutates `net`. Returns a list of errors for invalid actions."""
    errors = []
    for a in actions or []:
        kind, target = a.get("type"), a.get("target")
        if kind in ("open_line", "close_line"):
            if target not in net["lines"]:
                errors.append(f"Invalid action {kind}: unknown line {target}")
                continue
            net["lines"][target]["status"] = "open" if kind == "open_line" else "closed"
        elif kind == "shed_load":
            if target not in net["buses"]:
                errors.append(f"Invalid action shed_load: unknown bus {target}")
                continue
            net["shed"][target] = net["shed"].get(target, 0.0) + max(0.0, float(a.get("mw", 0)))
        elif kind == "dispatch_gen":
            if target not in net["gens"]:
                errors.append(f"Invalid action dispatch_gen: unknown generator {target}")
                continue
            g = net["gens"][target]
            g["dispatch_mw"] = min(g["gen_max_mw"], max(0.0, float(a.get("mw", 0))))
        elif kind == "deenergize_bus":
            if target not in net["buses"]:
                errors.append(f"Invalid action deenergize_bus: unknown bus {target}")
                continue
            if target not in net["deenergized"]:
                net["deenergized"].append(target)
        else:
            errors.append(f"Invalid action type {kind!r}")
    return errors


# --------------------------------------------------------------------------- thermal
def conductor_temp(amps: float, air_temp_c: float, wind_ms: float, solar_w_m2: float, elevation_m: float) -> float:
    """Solve I^2 R(T) + q_solar = q_conv + q_rad for T by bisection on [Ta, 250]."""
    rho_rel = math.exp(-max(elevation_m, 0.0) / 8500.0)  # thin air cools less
    h = 3.0 + 9.0 * math.sqrt(max(wind_ms, 0.5)) * rho_rel
    q_solar = ABSORPTIVITY * solar_w_m2 * D
    ta_k = air_temp_c + 273.0

    def balance(t):
        heat_in = amps * amps * R25 * (1 + ALPHA_R * (t - 25.0)) + q_solar
        q_conv = h * math.pi * D * (t - air_temp_c)
        q_rad = EMISSIVITY * SIGMA * math.pi * D * ((t + 273.0) ** 4 - ta_k**4)
        return heat_in - q_conv - q_rad

    lo, hi = air_temp_c, 250.0
    if balance(hi) > 0:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        if balance(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sag_at(temp_c: float, span_m: float, sag_ref_m: float) -> float:
    s = span_m
    l_ref = s + 8 * sag_ref_m**2 / (3 * s)
    l_t = l_ref * (1 + ALPHA_L * (temp_c - 25.0))
    return math.sqrt(max(0.0, 3 * s * (l_t - s) / 8))


def _line_frame(line: dict, buses: dict):
    a, b = buses[line["from_bus"]], buses[line["to_bus"]]
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    return ux, uy, uy, -ux  # unit along, right-hand normal


def clearance(line: dict, trees: list, buses: dict, wx: dict, sag_m: float):
    """Returns (min_clearance_m, cause, blowout_shift_m, swing_deg)."""
    _, _, nx, ny = _line_frame(line, buses)
    rad = math.radians(wx["wind_dir_deg"])
    w_perp = math.sin(rad) * nx + math.cos(rad) * ny  # +1 = wind blows toward the right-hand side
    gust_perp = wx["gust_ms"] * abs(w_perp)
    theta = math.atan(0.6 * gust_perp**2 * D / CONDUCTOR_WEIGHT_N_M)
    shift = sag_m * math.sin(theta) * (1.0 if w_perp >= 0 else -1.0)
    z = line["attach_height_m"] - sag_m * math.cos(theta)

    best, cause = 99.0, "none"
    for t in trees:
        if t["line"] != line["id"]:
            continue
        dx = abs(t["lateral_offset_m"] - shift)
        dz = max(0.0, z - t["height_m"])
        c = math.hypot(dx, dz)
        if c < best:
            best = c
            toward = shift * t["lateral_offset_m"] > 0 and abs(shift) > 0.3
            cause = "wind blowout toward trees" if toward else "thermal sag toward trees"
    return best, cause, shift, math.degrees(theta)


def fire_risk_index(gust_ms: float, humidity_pct: float, clearance_m: float) -> float:
    """0..1. Wind factor ramps 10->25 m/s, dryness ramps 40->10 %RH, vegetation ramps 8->2 m clearance.
    risk = sqrt(wind * dry) * (0.3 + 0.7 * vegetation). High only when it's windy AND dry; close trees amplify it."""
    wind_f = min(1.0, max(0.0, (gust_ms - 10.0) / 15.0))
    dry_f = min(1.0, max(0.0, (40.0 - humidity_pct) / 30.0))
    veg_f = min(1.0, max(0.0, (8.0 - clearance_m) / 6.0))
    return round(math.sqrt(wind_f * dry_f) * (0.3 + 0.7 * veg_f), 3)


# --------------------------------------------------------------------------- topology + power flow
def _islands(bus_ids: list, edges: list) -> list[list]:
    adj = {b: [] for b in bus_ids}
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    seen, out = set(), []
    for b in bus_ids:
        if b in seen:
            continue
        stack, comp = [b], []
        seen.add(b)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        out.append(sorted(comp, key=lambda s: int(s[1:])))
    return out


def power_flow(net: dict, closed_lines: list[str], active_buses: list[str], demand: dict):
    """DC power flow per island. Returns (flows_mw by line, served by bus, unserved by bus, gen output)."""
    lines, buses, gens = net["lines"], net["buses"], net["gens"]
    edges = [(lines[l]["from_bus"], lines[l]["to_bus"]) for l in closed_lines]
    flows = {l: 0.0 for l in lines}
    served = {b: 0.0 for b in buses}
    unserved = {b: demand.get(b, 0.0) for b in buses}
    gen_out = {g: 0.0 for g in gens}

    for island in _islands(active_buses, edges):
        iset = set(island)
        igens = [g for g in gens.values() if g["bus"] in iset]
        load = {b: demand.get(b, 0.0) for b in island}
        if not igens:
            continue  # no generation -> everything unserved
        slack = next((g for g in igens if g["slack"]), None) or max(igens, key=lambda g: g["gen_max_mw"])
        others = [g for g in igens if g is not slack]
        other_mw = sum(min(g["dispatch_mw"], g["gen_max_mw"]) for g in others)
        total_load = sum(load.values())
        slack_mw = total_load - other_mw

        if slack_mw > slack["gen_max_mw"]:  # shortfall: shed non-critical first, proportionally
            short = slack_mw - slack["gen_max_mw"]
            for crit in (False, True):
                pool = [b for b in island if buses[b]["critical"] == crit and load[b] > 0]
                pool_mw = sum(load[b] for b in pool)
                if pool_mw <= 0 or short <= 0:
                    continue
                cut = min(short, pool_mw)
                for b in pool:
                    load[b] -= cut * load[b] / pool_mw
                short -= cut
            slack_mw = slack["gen_max_mw"]
            other_scale = 1.0
        elif slack_mw < 0:  # surplus: back down the other units
            other_scale = total_load / other_mw if other_mw > 0 else 0.0
            slack_mw = 0.0
        else:
            other_scale = 1.0

        gen_out[slack["id"]] = slack_mw
        for g in others:
            gen_out[g["id"]] = min(g["dispatch_mw"], g["gen_max_mw"]) * other_scale
        for b in island:
            served[b] = load[b]
            unserved[b] = demand.get(b, 0.0) - load[b]

        if len(island) == 1:
            continue
        idx = {b: i for i, b in enumerate(island)}
        n = len(island)
        B = np.zeros((n, n))
        ilines = [l for l in closed_lines if lines[l]["from_bus"] in iset]
        for l in ilines:
            i, j = idx[lines[l]["from_bus"]], idx[lines[l]["to_bus"]]
            b = 1.0 / lines[l]["reactance_pu"]
            B[i, i] += b
            B[j, j] += b
            B[i, j] -= b
            B[j, i] -= b
        p = np.zeros(n)
        for b in island:
            p[idx[b]] -= load[b] / BASE_MVA
        for g in igens:
            p[idx[g["bus"]]] += gen_out[g["id"]] / BASE_MVA
        s = idx[slack["bus"]]
        keep = [i for i in range(n) if i != s]
        theta = np.zeros(n)
        theta[keep] = np.linalg.solve(B[np.ix_(keep, keep)], p[keep])
        for l in ilines:
            i, j = idx[lines[l]["from_bus"]], idx[lines[l]["to_bus"]]
            flows[l] = float((theta[i] - theta[j]) / lines[l]["reactance_pu"] * BASE_MVA)
    return flows, served, unserved, gen_out


def amps_from_mw(p_mw: float, kv: float) -> float:
    return abs(p_mw) * 1e6 / (math.sqrt(3) * kv * 1e3)


# --------------------------------------------------------------------------- main entry
def _snapshot(state) -> dict:
    snap = state.snapshot() if hasattr(state, "snapshot") else state
    return copy.deepcopy(snap)


def simulate(state, actions: list | None = None) -> dict:
    net = _snapshot(state)
    action_errors = apply_actions(net, actions)
    buses, lines, weather = net["buses"], net["lines"], net["weather"]
    mult = net.get("load_multiplier", 1.0)

    # 8. flood check (coastal buses)
    flooded = {}
    for b in buses.values():
        level = weather[b["zone"]]["flood_level_m"]
        flooded[b["id"]] = b["zone"] == "coast" and level > b["flood_threshold_m"] + max(b["elevation_m"], 0.0)

    # 1. topology
    deen = set(net["deenergized"])
    active = [b for b in buses if b not in deen]
    closed = [l for l, ln in lines.items() if ln["status"] == "closed" and ln["from_bus"] not in deen and ln["to_bus"] not in deen]
    demand, shed = {}, {}
    for b in buses.values():
        full = b["load_mw"] * mult
        s = min(full, net["shed"].get(b["id"], 0.0))
        shed[b["id"]] = s
        demand[b["id"]] = 0.0 if b["id"] in deen else full - s

    # 2. DC power flow
    flows, served, unserved, gen_out = power_flow(net, closed, active, demand)

    violations, line_out = [], {}
    for lid, ln in lines.items():
        wx = weather[ln["zone"]]
        energized = lid in closed
        p = flows[lid] if energized else 0.0
        amps = amps_from_mw(p, ln["voltage_kv"])  # 3. current
        temp = conductor_temp(amps, wx["air_temp_c"], wx["wind_speed_ms"], wx["solar_w_m2"], ln["mid_elevation_m"])  # 4
        sag = sag_at(temp, ln["span_m"], ln["sag_at_ref_m"])  # 5
        clr, cause, shift, swing = clearance(ln, net["trees"], buses, wx, sag)  # 6, 7
        loading = amps / ln["rating_amps"] * 100.0
        fire = fire_risk_index(wx["gust_ms"], wx["humidity_pct"], clr) if energized else 0.0  # 9
        lv = []
        if energized:
            if clr < CLEARANCE_MIN_M:
                lv.append(f"Line {lid}: clearance {clr:.1f} m < {CLEARANCE_MIN_M:.1f} m ({cause})")
            if loading > 100.0:
                lv.append(f"Line {lid}: loading {loading:.0f}% > 100% ({amps:.0f} A of {ln['rating_amps']:.0f} A)")
            if temp > ln["max_temp_c"]:
                lv.append(f"Line {lid}: conductor {temp:.0f} C > max {ln['max_temp_c']:.0f} C")
        violations.extend(lv)
        line_out[lid] = {
            "id": lid,
            "status": ln["status"],
            "energized": energized,
            "flow_mw": round(p, 2),
            "amps": round(amps, 1),
            "loading_pct": round(loading, 1),
            "temp_c": round(temp, 1),
            "sag_m": round(sag, 2),
            "clearance_m": round(clr, 2),
            "blowout_m": round(shift, 2),
            "swing_deg": round(swing, 1),
            "fire_risk": fire,
            "violations": lv,
        }

    bus_out, on_backup = {}, []
    for bid, b in buses.items():
        energized = bid not in deen and served[bid] + unserved[bid] >= 0 and _bus_connected_to_gen(bid, closed, active, net)
        uns = unserved[bid] + shed[bid] + (b["load_mw"] * mult if bid in deen else 0.0)
        uns = min(uns, b["load_mw"] * mult)
        backup = b["critical"] and uns > 0.01
        if backup:
            on_backup.append(bid)
        if flooded[bid] and energized:
            violations.append(
                f"Bus {bid} ({b['name']}): flooded ({weather[b['zone']]['flood_level_m']:.1f} m water > "
                f"{b['flood_threshold_m'] + b['elevation_m']:.1f} m pad) and still energized"
            )
        if backup and b["backup_hours"] <= 0:
            violations.append(f"Bus {bid} ({b['name']}): critical site unserved with no backup power")
        bus_out[bid] = {
            "id": bid,
            "served_mw": round(served[bid], 2),
            "unserved_mw": round(uns, 2),
            "shed_mw": round(shed[bid], 2),
            "flooded": flooded[bid],
            "energized": energized,
            "deenergized": bid in deen,
            "on_backup": backup,
        }
    violations.extend(action_errors)

    # 10. N-1 on the most loaded closed line
    n1 = _n1_check(net, closed, active, demand, line_out)

    total_uns = sum(b["unserved_mw"] for b in bus_out.values())
    return {
        "lines": line_out,
        "buses": bus_out,
        "gens": {g: round(v, 2) for g, v in gen_out.items()},
        "totals": {
            "unserved_mw": round(total_uns, 1),
            "customers_out": int(round(total_uns * CUSTOMERS_PER_MW)),
            "critical_on_backup": on_backup,
            "violation_count": len(violations),
            "load_mw": round(sum(b["load_mw"] for b in buses.values()) * mult, 1),
        },
        "violations": violations,
        "n1_warnings": n1,
        "actions": actions or [],
        "is_safe": _is_safe_raw(violations, bus_out, buses),
    }


def _bus_connected_to_gen(bid, closed, active, net) -> bool:
    if bid not in active:
        return False
    lines, gens = net["lines"], net["gens"]
    edges = [(lines[l]["from_bus"], lines[l]["to_bus"]) for l in closed]
    for isl in _islands(active, edges):
        if bid in isl:
            return any(g["bus"] in isl for g in gens.values())
    return False


def _n1_check(net, closed, active, demand, line_out) -> list[str]:
    """Trip the most loaded closed line and report new overloads / newly islanded load."""
    if not closed:
        return []
    worst = max(closed, key=lambda l: line_out[l]["loading_pct"])
    remaining = [l for l in closed if l != worst]
    _, _, base_uns, _ = power_flow(net, closed, active, demand)
    flows, _, uns, _ = power_flow(net, remaining, active, demand)
    warnings = []
    for l in remaining:
        ln = net["lines"][l]
        pct = amps_from_mw(flows[l], ln["voltage_kv"]) / ln["rating_amps"] * 100
        if pct > 100 and line_out[l]["loading_pct"] <= 100:
            warnings.append(f"If {worst} trips, {l} overloads to {pct:.0f}%")
    extra = sum(uns.values()) - sum(base_uns.values())
    if extra > 0.5:
        warnings.append(f"If {worst} trips, {extra:.0f} MW more load is islanded")
    return warnings


def _is_safe_raw(violations, bus_out, buses) -> bool:
    if violations:
        return False
    for bid, b in bus_out.items():
        if buses[bid]["critical"] and b["on_backup"] and buses[bid]["backup_hours"] <= 0:
            return False
    return True


def is_safe(result: dict) -> bool:
    """True when: no clearance violations, nothing over 100% loading or max temp, no energized flooded bus,
    every critical site served or on backup with hours remaining. (All of those land in result['violations'].)"""
    return bool(result.get("is_safe"))
