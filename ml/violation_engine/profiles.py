"""Configuration profiles: one saved configuration per map / site / scenario (Build Plan M0).

A profile (schemas/profile.schema.json) holds violation types and conditions on/off, rule
thresholds, model settings, optional modules and road configuration. Thresholds left out keep
rules.DEFAULTS, so a profile only lists what it changes. Profiles live in
configs/profiles/; the backend will store and edit the same documents (M5, M6).

    engine_params(profile)        -> the params dict rules.Engine takes (same shape as --params)
    disabled_conditions(profile)  -> condition ids whose events the engine drops

Usage:
    python ml/violation_engine/profiles.py default                 # print the default profile
    python ml/violation_engine/profiles.py check <profile.json> [...]
    python ml/violation_engine/run_violations.py <flight> --scene ... --profile <profile.json>
"""

import argparse
import json
import sys
from pathlib import Path

from rules import DEFAULTS
from schemas import conditions, profile_schema_errors

PROFILE_DIR = Path(__file__).resolve().parent / "configs" / "profiles"
REPO = Path(__file__).resolve().parents[2]
VIOLATION_TYPES = [k for k in DEFAULTS if k != "place_memory"]
OFF_BY_DEFAULT = {"red_light"}  # out of scope (Expected_Output Section 10, 2026-10-09)


def default_profile() -> dict:
    """Every threshold at its rules.DEFAULTS value; the in-scope types on, red light off."""
    return {
        "profile_version": 1,
        "name": "default",
        "description": "rules.DEFAULTS, all six in-scope types on, red light off",
        "applies_to": {"map": None, "site": None, "road_type": None, "scenario": None},
        "violations": {t: {"enabled": t not in OFF_BY_DEFAULT, "params": dict(DEFAULTS[t])} for t in VIOLATION_TYPES},
        "conditions": {},
        "place_memory": dict(DEFAULTS["place_memory"]),
        "modules": {"static_check": True, "evidence_clips": True, "learn_flow": False, "red_light": False,
                    "pedestrians": False, "surface_anomalies": False},
    }


def profile_errors(profile: dict) -> list[str]:
    """Schema errors plus what the schema can't see: threshold names rules.DEFAULTS doesn't know,
    condition ids not in conditions.json, and repo-relative road files that don't exist."""
    errs = profile_schema_errors(profile)
    if errs:
        return errs
    for t, v in profile.get("violations", {}).items():
        for k in v.get("params", {}):
            if k not in DEFAULTS[t]:
                errs.append(f"violations/{t}/params/{k}: unknown parameter (known: {', '.join(DEFAULTS[t])})")
    known = {c["id"] for c in conditions()}
    errs += [f"conditions/{c}: not in schemas/conditions.json" for c in profile.get("conditions", {}) if c not in known]
    for k in ("scene", "site", "zones"):
        p = profile.get("road", {}).get(k)
        if p and not (REPO / p).exists():
            errs.append(f"road/{k}: {p} not found (paths are relative to the repo root)")
    return errs


def load_profile(path: Path) -> dict:
    path = Path(path)
    if not path.exists() and (PROFILE_DIR / f"{path}.json").exists():
        path = PROFILE_DIR / f"{path}.json"  # a bare name: configs/profiles/<name>.json
    profile = json.loads(path.read_text(encoding="utf-8"))
    errs = profile_errors(profile)
    if errs:
        raise ValueError(f"profile {path}:\n  " + "\n  ".join(errs))
    return profile


def engine_params(profile: dict) -> dict:
    """{type: {**thresholds, "enabled": bool}, "place_memory": {...}}: overrides for rules.Engine."""
    out = {}
    for t, v in profile.get("violations", {}).items():
        out[t] = dict(v.get("params", {}))
        if "enabled" in v:
            out[t]["enabled"] = v["enabled"]
    for t in OFF_BY_DEFAULT:  # off unless the profile turns it on
        out.setdefault(t, {}).setdefault("enabled", False)
    if "place_memory" in profile:
        out["place_memory"] = dict(profile["place_memory"])
    cls_lim = profile.get("road", {}).get("class_speed_limits_kmh")
    if cls_lim:  # E4: per-class limits live with the road configuration, the speeding rule applies them
        out.setdefault("speeding", {})["class_limits_kmh"] = dict(cls_lim)
    return out


def disabled_conditions(profile: dict) -> set[str]:
    return {c for c, on in profile.get("conditions", {}).items() if not on}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("default", help="Print the default profile")
    ck = sub.add_parser("check", help="Validate profile files")
    ck.add_argument("files", type=Path, nargs="+")
    args = ap.parse_args()
    if args.cmd == "default":
        print(json.dumps(default_profile(), indent=1))
        return
    bad = 0
    for f in args.files:
        errs = profile_errors(json.loads(f.read_text(encoding="utf-8")))
        bad += bool(errs)
        print(f"{'OK  ' if not errs else 'FAIL'} {f}")
        for e in errs:
            print(f"     {e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
