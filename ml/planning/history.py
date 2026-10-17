"""Planner history (Build Plan M8; Expected_Output 7.5): decisions and measured outcomes as JSON lines,
and the re-ranking of future proposals by **measured** outcomes only.

One JSON object per line, append-only:
  {"kind": "decision", "rec_id", "rule_id", "countermeasure", "type", "decision": accepted|rejected|modified,
   "rationale", "by", "at", "modified_change"?}
  {"kind": "result", "rec_id", "rule_id", "countermeasure", "type", "metric", "baseline", "modified",
   "relative_change", "runs", "spread"?, "source", "at"}
  {"kind": "assessment", "rec_id", "by", "at", "rating" (-1|0|1), "note"}   (planner's explicit feedback)

Ranking: per rule (falling back to countermeasure), the mean relative change of the primary metric
over its measured results (negative = improvement), shrunk towards zero by n / (n + PRIOR_N). The
priority is multiplied by 1 - clamp(effect, -MAX_SHIFT, MAX_SHIFT). Accepting a proposal changes
nothing: an acceptance is not proof that it worked (Expected_Output 7.5).

Usage:
    python ml/planning/history.py --file ml/data/results/planning/history.jsonl --summary
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DECISIONS = ("accepted", "rejected", "modified")
PRIOR_N = 2.0  # pseudo-count of "no effect" results: one result moves the ranking only a third of the way
MAX_SHIFT = 0.5  # the history can move a priority by at most +-50 %
ASSESSMENT_WEIGHT = 0.05  # one explicit planner rating (+1 / -1) is worth a 5 % measured change


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class History:
    def __init__(self, path: Path):
        self.path = Path(path)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def _append(self, entry: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def record_decision(self, rec: dict, decision: str, rationale: str = "", by: str | None = None,
                        modified_change: dict | None = None, at: str | None = None) -> dict:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
        if decision == "modified" and not modified_change:
            raise ValueError("a 'modified' decision needs the changed simulation/action (modified_change)")
        e = {"kind": "decision", "rec_id": rec["id"], "rule_id": rec.get("rule_id"),
             "countermeasure": rec.get("action", {}).get("countermeasure"), "type": rec.get("type"),
             "decision": decision, "rationale": rationale, "by": by, "at": at or _now()}
        if modified_change:
            e["modified_change"] = modified_change
        return self._append(e)

    def record_result(self, rec: dict, metric: str, baseline: float, modified: float, runs: int = 1,
                      spread: float | None = None, source: str = "", at: str | None = None) -> dict:
        """A measured outcome (validate_recommendation.py). relative_change < 0 = the metric went down."""
        rel = (modified - baseline) / baseline if baseline else (0.0 if modified == baseline else math.copysign(1.0, modified))
        e = {"kind": "result", "rec_id": rec["id"], "rule_id": rec.get("rule_id"),
             "countermeasure": rec.get("action", {}).get("countermeasure"), "type": rec.get("type"),
             "metric": metric, "baseline": baseline, "modified": modified, "relative_change": round(rel, 4),
             "runs": runs, "spread": spread, "source": source, "at": at or _now()}
        return self._append(e)

    def record_assessment(self, rec_id: str, rating: int, note: str = "", by: str | None = None,
                          rule_id: str | None = None, at: str | None = None) -> dict:
        if rating not in (-1, 0, 1):
            raise ValueError("rating is -1, 0 or 1")
        return self._append({"kind": "assessment", "rec_id": rec_id, "rule_id": rule_id, "rating": rating,
                             "note": note, "by": by, "at": at or _now()})

    def decisions(self, rec_id: str | None = None) -> list[dict]:
        return [e for e in self.entries() if e["kind"] == "decision" and (rec_id is None or e["rec_id"] == rec_id)]

    def outcomes(self, key: str = "rule_id") -> dict[str, dict]:
        """Measured effect per rule (or countermeasure): n results, mean relative change, its spread, and
        the planners' explicit ratings. Lower mean = better."""
        res, ratings = defaultdict(list), defaultdict(list)
        for e in self.entries():
            k = e.get(key)
            if k is None:
                continue
            if e["kind"] == "result":
                res[k].append(e["relative_change"])
            elif e["kind"] == "assessment":
                ratings[k].append(e["rating"])
        out = {}
        for k in set(res) | set(ratings):
            v = res.get(k, [])
            mean = sum(v) / len(v) if v else 0.0
            sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else None
            out[k] = {"n_results": len(v), "mean_relative_change": round(mean, 4),
                      "sd": round(sd, 4) if sd is not None else None, "ratings": ratings.get(k, [])}
        return out


def adjustment(outcome: dict | None) -> dict:
    """Priority multiplier from one rule's outcomes (see module docstring)."""
    if not outcome:
        return {"multiplier": 1.0, "n_results": 0, "effect": 0.0,
                "uncertainty": "no measured outcome yet: ranking unchanged"}
    n = outcome["n_results"]
    shrink = n / (n + PRIOR_N)
    # improvement = metric went down: negative relative change raises the priority
    effect = -outcome["mean_relative_change"] * shrink + ASSESSMENT_WEIGHT * sum(outcome["ratings"])
    effect = max(-MAX_SHIFT, min(MAX_SHIFT, effect))
    unc = (f"{n} measured result(s), sd {outcome['sd']}" if n > 1 else
           f"{n} measured result: low certainty" if n == 1 else "planner ratings only, no measurement")
    return {"multiplier": round(1.0 + effect, 4), "n_results": n, "effect": round(effect, 4), "uncertainty": unc}


def rerank(recs: list[dict], history: History) -> list[dict]:
    """Multiply each recommendation's priority by its rule's history adjustment (countermeasure if the
    rule has no record) and re-sort. Adds priority.history."""
    by_rule, by_cm = history.outcomes("rule_id"), history.outcomes("countermeasure")
    for r in recs:
        o = by_rule.get(r.get("rule_id")) or by_cm.get(r.get("action", {}).get("countermeasure"))
        adj = adjustment(o)
        p = r["priority"]
        p.setdefault("base_score", p["score"])
        p["score"] = round(p["base_score"] * adj["multiplier"], 1)
        p["band"] = "high" if p["score"] >= 50 else "medium" if p["score"] >= 25 else "low"
        p["history"] = adj
    recs.sort(key=lambda r: (-r["priority"]["score"], r["id"]))
    return recs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    h = History(a.file)
    print(json.dumps({"entries": len(h.entries()), "by_rule": h.outcomes("rule_id"),
                      "by_countermeasure": h.outcomes("countermeasure")}, indent=1))


if __name__ == "__main__":
    main()
