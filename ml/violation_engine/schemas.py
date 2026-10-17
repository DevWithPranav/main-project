"""Shared JSON schemas: events and configuration profiles (Build Plan M0).

The schemas live in <repo>/schemas/ so the engine, the backend and the dashboards read the same
files:
    event.schema.json     one violation or anomaly event (events.py Event, PRD ViolationEvent)
    profile.schema.json   a configuration profile (profiles.py)
    scene.schema.json     a lane map with its road features (lane_map.py, road_features.py)
    conditions.json       the 34 conditions of docs/Expected_Output.md Section 4.2

condition_of() names the condition an engine event stands for, from conditions.json's match
rules (a tag, a value field, or the type's default).

Usage:
    python ml/violation_engine/schemas.py events <violations.json> [...]
    python ml/violation_engine/schemas.py profile <profile.json> [...]
    python ml/violation_engine/schemas.py scene <scene.json> [...]
"""

import argparse
import json
import sys
from functools import cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


@cache
def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validator(name: str):
    try:
        import jsonschema
    except ImportError:
        raise SystemExit("schema validation needs jsonschema: pip install jsonschema")
    schema = load(name)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def event_errors(events: list[dict]) -> list[str]:
    """Schema violations in a list of event dicts, as 'event_id: path: message'; empty if all pass."""
    v = _validator("event.schema.json")
    out = []
    for i, e in enumerate(events):
        eid = e.get("event_id", f"#{i}") if isinstance(e, dict) else f"#{i}"
        for err in v.iter_errors(e):
            out.append(f"{eid}: {'/'.join(map(str, err.absolute_path)) or '<root>'}: {err.message}")
    return out


def profile_schema_errors(profile: dict) -> list[str]:
    v = _validator("profile.schema.json")
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(profile)]


def scene_errors(scene: dict) -> list[str]:
    v = _validator("scene.schema.json")
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(scene)]


def conditions() -> list[dict]:
    return load("conditions.json")["conditions"]


def condition_of(event: dict) -> str | None:
    """The Section 4.2 condition id an engine event stands for: of the conditions of its type whose
    tag / value match holds, the highest priority (then the first listed); else the type's default
    condition; else None (e.g. red_light, out of scope)."""
    default, best = None, None
    for c in conditions():
        if c.get("engine_type") != event.get("type"):
            continue
        m = c.get("match", {})
        hit = ("tag" in m and m["tag"] in event.get("tags", [])) or \
            ("value" in m and all(event.get("value", {}).get(k) == v for k, v in m["value"].items()))
        if hit and (best is None or c.get("priority", 0) > best.get("priority", 0)):
            best = c
        if m.get("default"):
            default = c["id"]
    return best["id"] if best else default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["events", "profile", "scene"])
    ap.add_argument("files", type=Path, nargs="+")
    args = ap.parse_args()
    bad = 0
    for f in args.files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if args.kind == "events":
            errs = event_errors(data if isinstance(data, list) else [data])
            n = len(data) if isinstance(data, list) else 1
        elif args.kind == "scene":
            errs, n = scene_errors(data), len(data.get("lanes", []))
        else:
            from profiles import profile_errors
            errs, n = profile_errors(data), 1
        bad += bool(errs)
        print(f"{'OK  ' if not errs else 'FAIL'} {f} ({n} {args.kind})")
        for e in errs[:20]:
            print(f"     {e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
