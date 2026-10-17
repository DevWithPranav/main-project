"""Planning report of a traffic plan (Scenario_Builder_Plan.md Section 1 and 6): PDF, XLSX or CSV.

Contents follow the request: A scenario overview, B applied modifications, C statistical analysis, D recommendations,
E visual evidence, F final planning assessment. Every number carries the label of where it comes from, and the three
kinds are never mixed in one table:

    observed (recorded traffic)            speeds, queue, density, travel time around the plan before the change
    replay (measured on recorded traffic)  violations before / after, events removed / added
    rule of thumb (not measured)           risks and alternatives
    not simulated yet                      everything that needs a CARLA baseline-vs-modified run

`build_sections(ctx)` makes the content once; the three writers only lay it out.
"""

import csv
import io
from datetime import datetime, timezone

OBS = "observed (recorded traffic, before the change)"
REPLAY = "replay (measured on the same recorded traffic)"
RULE = "rule of thumb (not measured)"
PENDING = "not simulated yet (needs a CARLA run)"
VIRTUAL = "virtual simulation (simplified model, not CARLA)"

LIMITATIONS = [
    "The replay re-runs the violation engine on the traffic that was recorded: the same vehicles make the same movements. "
    "How drivers would respond to the change (new routes, slower or faster driving) is not modelled.",
    "Closures, barriers, cones, construction zones, accidents and breakdowns are approximated in the replay as restricted lanes.",
    "Props, rules and scenarios the replay cannot judge (signals, hypothetical vehicles, demand changes, weather, pedestrians) are kept "
    "in the plan but produce no numbers until they are simulated in CARLA.",
    "The virtual traffic simulation is a simplified model (car-following on the lane map, no lane changes, no junction conflicts, no signals); it is not CARLA and is not calibrated to the real traffic.",
    "Recommendations and risk checks are rule-based (rules of thumb); they are not machine-learning predictions and are not measurements.",
]


def _kind_label(ctx: dict, kind: str) -> str:
    return ((ctx.get("kinds") or {}).get(kind) or {}).get("label") or kind.replace("_", " ")


def _params(it: dict) -> str:
    p = it.get("params") or {}
    return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in p.items() if v not in (None, ""))


def _fmt(v, unit="", d=1):
    if v is None:
        return "-"
    return f"{round(float(v), d):g}{(' ' + unit) if unit else ''}"


def _traffic_text(cfg) -> str:
    """The traffic settings in one line (defaults when the plan has none)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "planning"))
    import traffic_config as tc
    c = tc.normalize(cfg)
    bg = c["background"]
    if not bg["enabled"]:
        return "no background traffic"
    mix = ", ".join(f"{k} {v:g}" for k, v in bg["mix"].items() if v)
    beh = ", ".join(f"{k} {v:g}" for k, v in bg["behaviour_mix"].items() if v)
    return (f"{tc.describe(cfg)}; vehicle mix % ({mix}); drivers % ({beh}); typical speed {bg['speed_factor_mean']:g} x limit (spread {bg['speed_factor_sd']:g}); "
            f"road weights urban {bg['road_weight']['urban']:g} / highway {bg['road_weight']['highway']:g}; {c['duration_s']:g} s after a {c['warmup_s']:g} s warm-up")


def build_sections(ctx: dict) -> list[dict]:
    plan, a = ctx["plan"], ctx.get("analysis") or {}
    obs, rep = a.get("observed"), a.get("replay")
    items = [(g, it) for g, k in (("prop", "props"), ("rule", "rules"), ("scenario", "scenarios")) for it in plan.get(k) or []]
    S: list[dict] = []
    pts = [(it["x"], it["y"]) for _, it in items if isinstance(it.get("x"), (int, float))]
    centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None
    S.append({"id": "A", "title": "A. Scenario overview", "type": "kv", "source": "", "rows": [
        ("Scenario", plan.get("name") or "(unnamed)"), ("Description", plan.get("description") or "-"),
        ("Map", plan.get("scene")), ("Selected location (map metres)", f"{centre[0]:.1f}, {centre[1]:.1f}" if centre else "-"),
        ("Plan", f"{ctx.get('plan_id') or 'not saved'}" + (f" version {ctx['version']}" if ctx.get("version") else "")),
        ("Traffic basis (recorded session)", ctx.get("session_id") or "none: rule-of-thumb checks only"),
        ("Analysis date", ctx.get("analysed_at") or "-"),
        ("Recorded duration analysed", _fmt(obs["duration_s"], "s", 0) if obs else "-"),
        ("Planning objectives", ", ".join(plan.get("objectives") or []) or "not set"),
        ("Custom traffic configuration", _traffic_text(plan.get("traffic"))),
        ("Evidence level", (a.get("evidence") or {}).get("level", "-")), ("Why that level", (a.get("evidence") or {}).get("why", "-")),
        ("Report made", ctx.get("generated_at")), ("By", ctx.get("by") or "-")]})
    S.append({"id": "B", "title": "B. Applied modifications", "type": "table", "source": "",
              "head": ["Group", "Kind", "Position (x, y)", "Settings", "Note"],
              "rows": [[g, _kind_label(ctx, it.get("kind", "")), f"{it.get('x', 0):.1f}, {it.get('y', 0):.1f}", _params(it), it.get("note") or ""] for g, it in items]})
    comp = a.get("compiled") or {}
    S.append({"id": "B2", "title": "B. How each item was evaluated", "type": "table", "source": "",
              "head": ["Item", "Judged by the replay?", "Detail"],
              "rows": [[_kind_label(ctx, e["kind"]), "yes", e["what"] + (f" ({e['note']})" if e.get("note") else "")] for e in comp.get("evaluated", [])]
              + [[_kind_label(ctx, e["kind"]), "no", e["reason"]] for e in comp.get("not_evaluated", [])]})
    v = a.get("validation") or {}
    S.append({"id": "B3", "title": "B. Validation and conflicts", "type": "table", "source": "", "head": ["Level", "Message"],
              "rows": [[m["level"], m["message"]] for k in ("errors", "conflicts", "warnings", "infos") for m in v.get(k, [])] or [["ok", "no problems found"]]})
    if obs:
        S.append({"id": "C1", "title": "C. Baseline traffic around the plan", "type": "kv", "source": OBS, "rows": [
            ("Area", f"{_fmt(obs['radius_m'], 'm', 0)} around {obs['centre'][0]:.0f}, {obs['centre'][1]:.0f}"), ("Vehicles seen", obs["vehicles"]),
            ("Mean speed", _fmt(obs["mean_speed_kmh"], "km/h")), ("85th-percentile speed", _fmt(obs["p85_speed_kmh"], "km/h")),
            ("Density", _fmt(obs["density_veh_per_km2"], "veh/km2", 0)), ("Flow", _fmt(obs["flow_veh_per_h"], "veh/h", 0)),
            ("Queue length (max / mean)", f"{_fmt(obs['queue']['max_m'], 'm')} / {_fmt(obs['queue']['mean_m'], 'm')}"),
            ("Travel time through the area (mean / p85)", f"{_fmt(obs['travel_time']['mean_s'], 's')} / {_fmt(obs['travel_time']['p85_s'], 's')} ({obs['travel_time']['vehicles']} vehicles)"),
            ("Conflicts (time-to-collision < 1.5 s)", obs["conflicts_ttc"]["count"])]})
    if rep:
        types = sorted(set(rep["baseline"]["by_type"]) | set(rep["modified"]["by_type"]))
        S.append({"id": "C2", "title": "C. Traffic violations before and after the plan", "type": "table", "source": REPLAY,
                  "head": ["Violation type", "Before", "After", "Change"],
                  "rows": [[t.replace("_", " "), rep["baseline"]["by_type"].get(t, 0), rep["modified"]["by_type"].get(t, 0),
                            rep["modified"]["by_type"].get(t, 0) - rep["baseline"]["by_type"].get(t, 0)] for t in types]
                  + [["TOTAL", rep["baseline"]["counted"], rep["modified"]["counted"], rep["modified"]["counted"] - rep["baseline"]["counted"]]],
                  "note": f"{rep['note']} Scope: {rep['scope']}; {rep['rows']} rows, {rep['tracks']} vehicles.", "chart": {t: (rep["baseline"]["by_type"].get(t, 0), rep["modified"]["by_type"].get(t, 0)) for t in types}})
        ev = lambda lst: [[(e.get("type") or "").replace("_", " "), e.get("condition") or "", _fmt(e.get("flag_s"), "s", 0), f"{e.get('x', 0):.0f}, {e.get('y', 0):.0f}" if e.get("x") is not None else ""] for e in lst]  # noqa: E731
        S.append({"id": "C3", "title": "C. Best case: violations the plan removes", "type": "table", "source": REPLAY, "head": ["Type", "Condition", "At", "Position"],
                  "rows": ev(rep["removed"]) or [["none", "", "", ""]]})
        S.append({"id": "C4", "title": "C. Worst case: new violations the plan creates", "type": "table", "source": REPLAY, "head": ["Type", "Condition", "At", "Position"],
                  "rows": ev(rep["added"]) or [["none", "", "", ""]]})
    vr = ctx.get("virtual")
    if vr:
        rows = []
        for m in vr.get("metrics", {}).values():
            b, pl = m["baseline"], m["plan"]
            if b["mean"] is None and pl["mean"] is None:
                continue
            f = lambda x: "-" if x["mean"] is None else f"{x['mean']:g}" + (f" ± {x['ci95']:g}" if x.get("ci95") is not None else "")  # noqa: E731
            rows.append([m["label"] + (f" ({m['unit']})" if m.get("unit") else ""), f(b), f(pl), "-" if m["diff"] is None else f"{m['diff']:+g}", m["verdict"]])
        S.append({"id": "C6", "title": "C. Virtual traffic simulation: baseline vs plan", "type": "table", "source": VIRTUAL,
                  "head": ["Metric", "Baseline", "With the plan", "Change", "Verdict"], "rows": rows,
                  "note": f"{vr.get('seeds')} runs per variant of {vr.get('duration_s')} s after a {vr.get('warmup_s')} s warm-up; mean ± 95 % interval over the seeds. "
                          + " ".join(vr.get("notes", []))})
    S.append({"id": "C5", "title": "C. Metrics not available yet", "type": "table", "source": PENDING, "head": ["Metric", "Status"],
              "rows": [[m, "not simulated yet" if not vr else "virtual model only, not CARLA"] for m in a.get("pending_metrics", [])] + [["congestion heatmaps", "not simulated yet"], ["intersection performance after the change", "not simulated yet"]]})
    S.append({"id": "D1", "title": "D. Risks", "type": "table", "source": RULE, "head": ["Level", "Risk"],
              "rows": [[r["level"], r["message"]] for r in a.get("risks", [])] or [["-", "no rule-of-thumb risks found"]]})
    S.append({"id": "D2", "title": "D. Alternatives (rule-based recommendations)", "type": "table", "source": RULE,
              "head": ["Alternative", "Benefit", "Risk", "Trade-off", "Evidence"],
              "rows": [[x["title"], x["benefit"], x["risk"], x["tradeoff"], f"{x['evidence']}: {x['evidence_note']}"] for x in a.get("alternatives", [])] or [["none", "", "", "", ""]]})
    cmp_rows = ctx.get("comparison") or []
    if len(cmp_rows) > 1:
        S.append({"id": "D3", "title": "D. Scenario comparison (same recorded traffic)", "type": "table", "source": REPLAY,
                  "head": ["Scenario", "Evidence", "Violations before", "Violations after", "New", "Removed"],
                  "rows": [[r["name"], r["evidence"], r["before"], r["after"], r["added"], r["removed"]] for r in cmp_rows]})
    shots = ctx.get("screenshots") or []
    S.append({"id": "E", "title": "E. Visual evidence", "type": "images", "source": "", "images": shots,
              "note": f"{len(shots)} digital-twin screenshot(s). Congestion heatmaps and vehicle-movement visualisations need a CARLA run (not simulated yet)."})
    # F: facts only, from the numbers above
    best = None
    scored = [r for r in cmp_rows if r.get("after") is not None]
    if scored:
        best = min(scored, key=lambda r: r["after"])
    summary = []
    if rep:
        d = rep["modified"]["counted"] - rep["baseline"]["counted"]
        summary.append(f"On the recorded traffic the plan changes counted violations from {rep['baseline']['counted']} to {rep['modified']['counted']} ({d:+d}); "
                       f"{len(rep['removed'])} removed, {len(rep['added'])} new.")
    else:
        summary.append("No replay result is attached: nothing in the plan changes a rule the replay can judge, or no recorded session was analysed.")
    if best and len(scored) > 1:
        summary.append(f"Fewest violations among the compared scenarios: {best['name']} ({best['after']}). This is one objective only; travel time, throughput and safety are not simulated, so no overall ranking is claimed.")
    nxt = ["Run the plan in CARLA (baseline vs modified, several seeds) to measure travel time, queue, throughput and delay.",
           "Resolve the validation conflicts and warnings listed in section B." if (v.get("conflicts") or v.get("warnings") or v.get("errors")) else "Review the risks in section D with the road owner."]
    S.append({"id": "F", "title": "F. Final planning assessment", "type": "text", "source": "",
              "paragraphs": [("Summary of findings", summary), ("Most promising scenario", [f"{best['name']}: {best['after']} violations after (replay)." if best else "Not determined: compare at least two scenarios on the same session."]),
                             ("Potential risks", [r["message"] for r in a.get("risks", [])] or ["none found by the rule-of-thumb checks"]),
                             ("Recommended next actions", nxt), ("Limitations and assumptions", LIMITATIONS)]})
    return S


# ---------------------------------------------------------------------------- writers

def to_csv(ctx: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["section", "row", "column", "value", "source"])
    for s in build_sections(ctx):
        if s["type"] == "kv":
            for k, v in s["rows"]:
                w.writerow([s["title"], k, "value", v, s["source"]])
        elif s["type"] == "table":
            for i, r in enumerate(s["rows"], 1):
                for h, v in zip(s["head"], r):
                    w.writerow([s["title"], i, h, v, s["source"]])
        elif s["type"] == "text":
            for title, lines in s["paragraphs"]:
                for i, ln in enumerate(lines, 1):
                    w.writerow([s["title"], title, i, ln, ""])
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx(ctx: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="DDE3EA")
    for s in build_sections(ctx):
        if s["type"] == "images":
            continue
        ws = wb.create_sheet(s["id"][:31])
        ws.append([s["title"]])
        ws["A1"].font = Font(bold=True, size=13)
        if s["source"]:
            ws.append(["Source of these numbers:", s["source"]])
        ws.append([])
        if s["type"] == "kv":
            for k, v in s["rows"]:
                ws.append([k, v if isinstance(v, (int, float)) else str(v)])
        elif s["type"] == "table":
            ws.append(s["head"])
            for c in ws[ws.max_row]:
                c.font, c.fill = Font(bold=True), head_fill
            for r in s["rows"]:
                ws.append([x if isinstance(x, (int, float)) else str(x) for x in r])
        else:
            for title, lines in s["paragraphs"]:
                ws.append([title])
                ws[ws.max_row][0].font = Font(bold=True)
                for ln in lines:
                    ws.append(["", ln])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(70, max(12, max(len(str(c.value or "")) for c in col) + 2))
        for row in ws.iter_rows():
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(ctx: dict) -> bytes:
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    st = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=st["Normal"], fontSize=7.5, leading=9.5)
    cell = ParagraphStyle("cell", parent=st["Normal"], fontSize=8, leading=10)
    tag = ParagraphStyle("tag", parent=st["Normal"], fontSize=7.5, textColor=colors.HexColor("#495057"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Traffic plan report: {ctx['plan'].get('name', '')}")
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde3ea")),
                       ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    W = A4[0] - 28 * mm
    story = [Paragraph("Traffic plan report", st["Title"]), Paragraph(esc(ctx["plan"].get("name") or "(unnamed plan)"), st["Heading2"]),
             Paragraph("Numbers are labelled by where they come from: observed on recorded traffic, replay (measured on the same traffic with the plan applied), "
                       "rule of thumb (not measured), or not simulated yet.", small), Spacer(1, 4 * mm)]
    for s in build_sections(ctx):
        block = [Paragraph(esc(s["title"]), st["Heading3"])]
        if s["source"]:
            block.append(Paragraph(f"Source: {esc(s['source'])}", tag))
        if s["type"] == "kv":
            t = Table([[Paragraph(f"<b>{esc(k)}</b>", cell), Paragraph(esc(v), cell)] for k, v in s["rows"]], colWidths=[W * 0.34, W * 0.66])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            block.append(t)
        elif s["type"] == "table":
            n = len(s["head"])
            data = [[Paragraph(f"<b>{esc(h)}</b>", cell) for h in s["head"]]] + [[Paragraph(esc(x), cell) for x in r] for r in s["rows"]]
            t = Table(data, colWidths=[W / n] * n if n != 5 else [W * 0.2, W * 0.22, W * 0.2, W * 0.2, W * 0.18], repeatRows=1)
            t.setStyle(grid)
            block.append(t)
            if s.get("chart"):
                ch = s["chart"]
                d = Drawing(W, 150)
                bc = VerticalBarChart()
                bc.x, bc.y, bc.width, bc.height = 30, 30, W - 50, 100
                bc.data = [[v[0] for v in ch.values()], [v[1] for v in ch.values()]]
                bc.categoryAxis.categoryNames = [k.replace("_", " ") for k in ch]
                bc.categoryAxis.labels.fontSize = 6
                bc.categoryAxis.labels.angle = 20
                bc.categoryAxis.labels.boxAnchor = "ne"
                bc.valueAxis.valueMin = 0
                bc.bars[0].fillColor, bc.bars[1].fillColor = colors.HexColor("#868e96"), colors.HexColor("#4c6ef5")
                d.add(bc)
                block += [Spacer(1, 2 * mm), d, Paragraph("Grey: before. Blue: after (replay on the recorded traffic).", tag)]
            if s.get("note"):
                block.append(Paragraph(esc(s["note"]), small))
        elif s["type"] == "text":
            for title, lines in s["paragraphs"]:
                block.append(Paragraph(f"<b>{esc(title)}</b>", cell))
                block += [Paragraph("&bull; " + esc(ln), cell) for ln in lines]
        elif s["type"] == "images":
            block.append(Paragraph(esc(s["note"]), small))
            for img in s["images"]:
                try:
                    im = Image(io.BytesIO(img))
                    ratio = im.imageHeight / im.imageWidth
                    im.drawWidth, im.drawHeight = W, W * ratio
                    block += [Spacer(1, 2 * mm), im]
                except Exception:  # noqa: BLE001 - a bad screenshot must not fail the report
                    block.append(Paragraph("(a screenshot could not be read)", small))
        block.append(Spacer(1, 3 * mm))
        story += block  # headings keep with the next flowable (reportlab's sample styles)
    doc.build(story)
    return buf.getvalue()


def make_ctx(plan: dict, analysis: dict | None, comparison: list[dict] | None, screenshots: list[bytes], by: str,
             kinds: dict | None = None, plan_id: str | None = None, version: int | None = None, session_id: str | None = None,
             analysed_at: str | None = None, virtual: dict | None = None) -> dict:
    return {"virtual": virtual, "plan": plan, "analysis": analysis, "comparison": comparison or [], "screenshots": screenshots, "by": by, "kinds": kinds or {},
            "plan_id": plan_id, "version": version, "session_id": session_id or plan.get("session_id"), "analysed_at": analysed_at,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
