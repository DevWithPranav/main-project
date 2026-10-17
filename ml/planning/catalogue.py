"""Countermeasure catalogue: the sourced rule-of-thumb effects behind `expected_impact` (Build Plan M8).

Every number here is a published claim (docs/research/M8_recommendations.md), never our measurement.
recommend.py shows it only as "projected (rule-of-thumb from <source>)". A countermeasure without a
retrieved number has reduction=None: the projection is then left empty and says so.

reduction = (low, high) fraction of the source's metric removed (CMF 0.83 -> 0.17).
"""

CLEARINGHOUSE = "https://cmfclearinghouse.fhwa.dot.gov/resources_about.php"

COUNTERMEASURES: dict[str, dict] = {
    "speed_camera": {
        "name": "Speed camera at one spot",
        "reduction": (0.17, 0.42),
        "source": "HSM automated speed enforcement CMF 0.83 (FHWA CMF Clearinghouse) to TfNSW 2014 fixed-camera "
                  "review (-42 % crashes at sites); Clearinghouse entries range CMF 0.37-1.07",
        "url": CLEARINGHOUSE,
        "metric": "crashes at the treated sites",
    },
    "average_speed_camera": {
        "name": "Average-speed cameras over a stretch",
        "reduction": (0.37, 0.85),
        "source": "UK case-study sites quoted in ACT Hansard 2011 (killed/serious-injury crash rate)",
        "url": "https://hansard.act.gov.au/hansard/7th-assembly/2011/HTML/week07/2958.htm",
        "metric": "killed and serious-injury crashes",
    },
    "speed_limit_review": {
        "name": "Lower the speed limit (with new signs)",
        "reduction": None,
        "source": "Power model (Nilsson; Elvik 2009 exponents, all injury 1.6 / fatal 4.1) converts a measured "
                  "mean-speed change into a crash change: needs the CARLA run's speed change first",
        "url": "https://www.fhwa.dot.gov/publications/research/safety/17098/003.cfm",
        "metric": "injury crashes, from the measured mean-speed change",
    },
    "traffic_calming": {
        "name": "Slow cars down (speed humps, narrower road, raised crossings)",
        "reduction": None,
        "source": "iRAP methodology (traffic-calming risk factor); numeric factor not retrieved",
        "url": "https://irap.org/?p=30531",
        "metric": "fatal and serious injuries",
    },
    "wrong_way_signage": {
        "name": "WRONG WAY / NO ENTRY signs and arrows on the road",
        "reduction": None,
        "source": "FHWA-HRT-23-035: statistically significant crash-risk reduction at ramps; CMF values not retrieved",
        "url": "https://highways.dot.gov/sites/fhwa.dot.gov/files/FHWA-HRT-23-035.pdf",
        "metric": "wrong-way crashes",
    },
    "one_way_barrier": {
        "name": "Make the road one-way with barriers",
        "reduction": None,
        "source": "PRD 21.2 rule set; no sourced factor",
        "url": "",
        "metric": "wrong-way movements",
    },
    "median_closure": {
        "name": "Close the gap in the middle of the road (barrier or posts)",
        "reduction": None,
        "source": "No sourced factor retrieved (FHWA lists median barriers as a proven countermeasure for cross-median crashes)",
        "url": "",
        "metric": "U-turn movements",
    },
    "no_u_turn_signage": {
        "name": "NO U-TURN signs and a turning ban",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "U-turn movements",
    },
    "no_stopping_enforcement": {
        "name": "Ban stopping (signs, road markings, enforcement)",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "stopping events",
    },
    "stopping_bay": {
        "name": "A proper stopping place or emergency lay-by",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "stopping events on the carriageway",
    },
    "crossing_visibility": {
        "name": "Make the crossing easier to see (bright markings, stop line set back, signs)",
        "reduction": (0.23, 0.48),
        "source": "FHWA Proven Safety Countermeasures / STEP: crash reduction factor 23-48 %",
        "url": "https://baltometro.org/sites/default/files/bmc_documents/committee/presentations/brss/"
               "BRSS220922pres_FHWA-Safety-Countermeasures.pdf",
        "metric": "pedestrian crashes",
    },
    "keep_clear_box": {
        "name": "KEEP CLEAR / yellow box markings in front of the crossing",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "crossing blockages",
    },
    "signal_timing": {
        "name": "Change the traffic light timing",
        "reduction": None,
        "source": "No sourced factor retrieved; measured in CARLA (queues, travel time, blockages)",
        "url": "",
        "metric": "queues and blockages",
    },
    "signal_installation": {
        "name": "Put in traffic lights",
        "reduction": (0.70, 0.80),
        "source": "PRD 21.3 rule-of-thumb ('signal-compliance study benchmarks', original study not cited)",
        "url": "",
        "metric": "red-light violations",
    },
    "marking_refresh": {
        "name": "Repaint the lane lines (solid where lane changes are unsafe)",
        "reduction": None,
        "source": "iRAP delineation risk factor; numeric factor not retrieved",
        "url": "https://irap.org/?p=30531",
        "metric": "lane-change crashes",
    },
    "delineators": {
        "name": "Flexible posts or rumble strips along the line",
        "reduction": None,
        "source": "No sourced factor retrieved for urban delineator posts",
        "url": "",
        "metric": "line crossings",
    },
    "lane_use_signage": {
        "name": "Lane arrows and signs over the road before the split",
        "reduction": None,
        "source": "No sourced factor retrieved",
        "url": "",
        "metric": "wrong-lane movements",
    },
    "lane_allocation": {
        "name": "Change how the lanes are used, to cut queues",
        "reduction": None,
        "source": "No sourced factor retrieved; measured in CARLA",
        "url": "",
        "metric": "queues and travel time",
    },
    "enforcement_schedule": {
        "name": "Change when the police or cameras check",
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
    out = {"label": "estimate", "countermeasure": c["name"],
           "before": {"events": before, "period_s": period_s,
                      "per_hour": round(before * 3600.0 / period_s, 2) if period_s else None},
           "source": {"name": c["source"], "url": c["url"], "metric": c["metric"]},
           "metric_note": "The studies count " + c["metric"] + "; we count rule breaking. "
                          "Use this as a rough direction, not a forecast."}
    if period_s:
        out["before"]["rate_note"] = f"based on {period_s / 60:.0f} minutes of video"
    if c["reduction"] is None:
        out["metric_note"] = f"No study gives a number for this; a simulator test can measure the effect on {c['metric']}."
        out.update(projected_after=None, reduction=None,
                   basis="We do not know yet how much this would help; a simulator test can measure it")
    else:
        lo, hi = c["reduction"]
        out.update(reduction={"low": lo, "high": hi},
                   projected_after={"events_low": round(before * (1 - hi), 1), "events_high": round(before * (1 - lo), 1)},
                   basis="a rough guide from published studies")
    return out
