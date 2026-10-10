"""Countermeasure catalogue: the sourced rule-of-thumb effects behind `expected_impact` (Build Plan M8).

Every number here is a published claim (docs/research/M8_recommendations.md), never our measurement.
recommend.py shows it only as "projected (rule-of-thumb from <source>)". A countermeasure without a
retrieved number has reduction=None: the projection is then left empty and says so.

reduction = (low, high) fraction of the source's metric removed (CMF 0.83 -> 0.17).
"""

CLEARINGHOUSE = "https://cmfclearinghouse.fhwa.dot.gov/resources_about.php"

COUNTERMEASURES: dict[str, dict] = {
    "speed_camera": {
        "name": "Automated speed enforcement (fixed speed camera)",
        "reduction": (0.17, 0.42),
        "source": "HSM automated speed enforcement CMF 0.83 (FHWA CMF Clearinghouse) to TfNSW 2014 fixed-camera "
                  "review (-42 % crashes at sites); Clearinghouse entries range CMF 0.37-1.07",
        "url": CLEARINGHOUSE,
        "metric": "crashes at the treated sites",
    },
    "average_speed_camera": {
        "name": "Point-to-point (average speed) enforcement",
        "reduction": (0.37, 0.85),
        "source": "UK case-study sites quoted in ACT Hansard 2011 (killed/serious-injury crash rate)",
        "url": "https://hansard.act.gov.au/hansard/7th-assembly/2011/HTML/week07/2958.htm",
        "metric": "killed and serious-injury crashes",
    },
    "speed_limit_review": {
        "name": "Speed limit review (lower posted limit with signage)",
        "reduction": None,
        "source": "Power model (Nilsson; Elvik 2009 exponents, all injury 1.6 / fatal 4.1) converts a measured "
                  "mean-speed change into a crash change: needs the CARLA run's speed change first",
        "url": "https://www.fhwa.dot.gov/publications/research/safety/17098/003.cfm",
        "metric": "injury crashes, from the measured mean-speed change",
    },
    "traffic_calming": {
        "name": "Traffic calming (speed humps, narrowing, raised tables)",
        "reduction": None,
        "source": "iRAP methodology (traffic-calming risk factor); numeric factor not retrieved",
        "url": "https://irap.org/?p=30531",
        "metric": "fatal and serious injuries",
    },
    "wrong_way_signage": {
        "name": "WRONG WAY / DO NOT ENTER signs and wrong-way pavement arrows",
        "reduction": None,
        "source": "FHWA-HRT-23-035: statistically significant crash-risk reduction at ramps; CMF values not retrieved",
        "url": "https://highways.dot.gov/sites/fhwa.dot.gov/files/FHWA-HRT-23-035.pdf",
        "metric": "wrong-way crashes",
    },
    "one_way_barrier": {
        "name": "One-way conversion with physical barriers (PRD 21.2)",
        "reduction": None,
        "source": "PRD 21.2 rule set; no sourced factor",
        "url": "",
        "metric": "wrong-way movements",
    },
    "median_closure": {
        "name": "Close or restrict the median opening (barrier / bollards)",
        "reduction": None,
        "source": "No sourced factor retrieved (FHWA lists median barriers as a proven countermeasure for cross-median crashes)",
        "url": "",
        "metric": "U-turn movements",
    },
    "no_u_turn_signage": {
        "name": "NO U-TURN signs and turn restriction",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "U-turn movements",
    },
    "no_stopping_enforcement": {
        "name": "No-stopping restriction: signs, markings, enforcement",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "stopping events",
    },
    "stopping_bay": {
        "name": "Designated stopping / emergency lay-by",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "stopping events on the carriageway",
    },
    "crossing_visibility": {
        "name": "Crosswalk visibility enhancements (high-visibility markings, advance stop line, signs)",
        "reduction": (0.23, 0.48),
        "source": "FHWA Proven Safety Countermeasures / STEP: crash reduction factor 23-48 %",
        "url": "https://baltometro.org/sites/default/files/bmc_documents/committee/presentations/brss/"
               "BRSS220922pres_FHWA-Safety-Countermeasures.pdf",
        "metric": "pedestrian crashes",
    },
    "keep_clear_box": {
        "name": "KEEP CLEAR / yellow-box markings in front of the crossing",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "crossing blockages",
    },
    "signal_timing": {
        "name": "Signal timing change (green split / clearance)",
        "reduction": None,
        "source": "No sourced factor retrieved; measured in CARLA (queues, travel time, blockages)",
        "url": "",
        "metric": "queues and blockages",
    },
    "signal_installation": {
        "name": "Traffic signal installation (PRD 21.3)",
        "reduction": (0.70, 0.80),
        "source": "PRD 21.3 rule-of-thumb ('signal-compliance study benchmarks', original study not cited)",
        "url": "",
        "metric": "red-light violations",
    },
    "marking_refresh": {
        "name": "Lane marking refresh (solid line where changes are unsafe)",
        "reduction": None,
        "source": "iRAP delineation risk factor; numeric factor not retrieved",
        "url": "https://irap.org/?p=30531",
        "metric": "lane-change crashes",
    },
    "delineators": {
        "name": "Flexible delineator posts / rumble strips along the line",
        "reduction": None,
        "source": "No sourced factor retrieved for urban delineator posts",
        "url": "",
        "metric": "line crossings",
    },
    "lane_use_signage": {
        "name": "Lane-use arrows and overhead lane signs before the split",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "wrong-lane movements",
    },
    "lane_allocation": {
        "name": "Lane allocation change / queue management",
        "reduction": None,
        "source": "No sourced factor retrieved; measured in CARLA",
        "url": "",
        "metric": "queues and travel time",
    },
    "enforcement_schedule": {
        "name": "Enforcement schedule adjustment (PRD 21.2)",
        "reduction": None,
        "source": "PRD 21.2 rule set; no sourced factor",
        "url": "",
        "metric": "violations in the peak window",
    },
}


def projected(key: str, before: int, period_s: float | None) -> dict:
    """expected_impact for countermeasure `key` applied to `before` events: a projected range when the
    catalogue has a sourced factor, else an explicit 'not quantified'."""
    c = COUNTERMEASURES[key]
    out = {"label": "projected estimate", "countermeasure": c["name"],
           "before": {"events": before, "period_s": period_s,
                      "per_hour": round(before * 3600.0 / period_s, 2) if period_s else None},
           "source": {"name": c["source"], "url": c["url"], "metric": c["metric"]},
           "metric_note": "The source measures " + c["metric"] + "; our counts are detected violations. "
                          "Treat the projection as a direction and order of magnitude, not a forecast."}
    if period_s:
        out["before"]["rate_note"] = f"rate extrapolated from {period_s:.0f} s of observation"
    if c["reduction"] is None:
        out["metric_note"] = f"No published factor used; the CARLA validation run measures the effect on {c['metric']}."
        out.update(projected_after=None, reduction=None,
                   basis="not quantified: no sourced factor; measure it with the CARLA validation run")
    else:
        lo, hi = c["reduction"]
        out.update(reduction={"low": lo, "high": hi},
                   projected_after={"events_low": round(before * (1 - hi), 1), "events_high": round(before * (1 - lo), 1)},
                   basis=f"projected (rule-of-thumb from {c['source']})")
    return out
