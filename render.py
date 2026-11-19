"""Offline reports, standalone playback, PDF, and browser validation."""
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

if __package__:
    from .accounting import summarize
    from .sources import HARNESSES
else:
    from accounting import summarize
    from sources import HARNESSES

ASSETS = Path(__file__).parent / "assets"
REPORT_CSS = """
:root{--ink:#203547;--muted:#637689;--gold:#c58c2f;--teal:#2c8582;--navy:#102637}
*{box-sizing:border-box}body{font:15px/1.45 system-ui,sans-serif;color:var(--ink);background:#f3f6f8;margin:0;overflow-wrap:anywhere}
main{max-width:1120px;margin:20px auto;padding:0 24px}header{background:var(--navy);color:#ecf4f8;border-radius:14px;padding:18px}
h1{font-size:34px;line-height:1.15;letter-spacing:-.03em;margin:6px 0 10px}h2{font-size:22px;border-bottom:1px solid #dde5eb;padding-bottom:5px;margin:12px 0 8px}section>h2:first-child{margin-top:0}
h3{font-size:17px;margin:8px 0 5px}p,ul,ol{margin:8px 0}.eyebrow{font-size:11px;font-weight:750;letter-spacing:.16em}.muted{color:var(--muted)}
section{background:white;border:1px solid #dde5eb;border-radius:10px;padding:16px;margin:12px 0}a{color:#1f6b83}header a{color:#ffd58c}
nav{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0;font-size:13px}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:12px 0}
.stat{display:flex;flex-direction:column;justify-content:space-between;background:white;border:1px solid #dde5eb;border-radius:10px;padding:12px}.stat b{display:block;color:var(--teal);font-size:27px}.stat span{font-size:12px}
.report-period{font-size:12px;margin:6px 0 12px}.timing-summary{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 24px;margin:8px 0;font-size:14px}.timing-summary strong{color:var(--teal);font-size:17px}.timing-summary small{font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:6px 8px;border-bottom:1px solid #dde5eb;text-align:left;vertical-align:top}th{background:#edf3f6}.table-scroll{overflow:auto}
.decision{border-left:3px solid var(--gold);padding:4px 12px;margin:12px 0}.quote,.message{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.45 system-ui,sans-serif;background:#f5f8fa;padding:10px;border-left:3px solid var(--teal)}
.agent{border-left-color:#839daf}.source{font-size:11px;color:var(--muted)}.playback-frame{width:100%;height:1250px;border:1px solid #dde5eb;border-radius:10px}
.session-log-frame{display:block;width:100%;height:clamp(300px,50vh,600px);border:0}.session-log-heading{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px 16px}.session-log-heading h2{flex:1;margin:0}.session-log-heading input{max-width:100%;width:300px;font:inherit;font-size:13px;padding:8px 10px;border:1px solid var(--muted);border-radius:6px}
.record-usage{font-size:11px;color:var(--teal);margin:0 0 8px;padding:6px 10px;background:#edf6f4;border-left:3px solid var(--teal)}
.model-comparison table{min-width:850px;font-variant-numeric:tabular-nums}.model-comparison th:nth-child(n+2),.model-comparison td:nth-child(n+2){text-align:right}.model-comparison td:nth-child(5),.model-comparison td:nth-child(7){font-weight:700}.comparison-legend{display:flex;gap:18px;font-size:12px}.comparison-legend span::before{content:"";display:inline-block;width:12px;height:8px;margin-right:6px;background:var(--teal)}.comparison-legend .cost-share::before{background:var(--gold)}.model-chart text{fill:var(--ink)}
.playback-poster{display:none;width:100%}.chart{display:block;width:100%;height:auto}details{margin:6px 0}summary{cursor:pointer;font-weight:650}code{font-size:.85em}.detail-scroll{max-height:420px;overflow:auto}
@media(max-width:600px){main{padding:0 14px;margin:16px auto}h1{font-size:27px}section{padding:16px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.stat.cost{grid-column:1/-1;flex-direction:row;justify-content:flex-start;align-items:baseline;gap:8px;flex-wrap:wrap}header{padding:20px}}
@media print{body{background:white;font-size:10pt}main{padding:0;margin:0;max-width:none}nav,.playback-frame,.session-log-frame,.session-log-heading input{display:none}.playback-poster{display:block}
 .model-comparison table{min-width:0;font-size:9px}
section{border:0;padding:0;margin:12px 0}header{background:#edf3f6;color:var(--ink);padding:14px}.stats{grid-template-columns:repeat(5,minmax(0,1fr))}.stat{padding:8px}.stat.cost{grid-column:auto;flex-direction:column;align-items:stretch;justify-content:space-between}
h2,h3,summary{break-after:avoid}.decision,.stat,tr{break-inside:avoid}.quote,.message{font-size:9pt}details{break-inside:auto}.detail-scroll{max-height:none;overflow:visible}a{color:inherit;text-decoration:none}}
"""


def number(value):
    return f"{value:,}" if value is not None else "Unknown"


def duration(milliseconds):
    if milliseconds is None:
        return "Unknown"
    seconds = int((milliseconds + 500) // 1000)
    if not seconds:
        return "0s"
    days, remaining = divmod(seconds, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    return " ".join(f"{value:g}{unit}" for value, unit in
                    ((days, "d"), (hours, "h"), (minutes, "m"), (seconds, "s")) if value)


def review_prose(value):
    """Use user terminology in reviewed prose, never in source messages."""
    def replace(match):
        word = "users" if match[0].lower() == "humans" else "user"
        return word.upper() if match[0].isupper() else word.capitalize() if match[0][0].isupper() else word
    return re.sub(r"\bhumans?\b", replace, value, flags=re.IGNORECASE)


def input_number(total):
    return number(total["input"]) + ("+ (incomplete)" if not total.get("input_complete", True) else "")


def cost_component(total, key):
    part = (total.get("cost_components") or {}).get(key)
    return f'{number(part["tokens"])} / ${part["estimated_usd"]:,.2f}' if part else "Unknown"


def spend(total):
    if not total["calls"]:
        return "Unknown (no recorded usage)"
    lower = total.get("estimated_usd_min", total["estimated_usd"])
    upper = total.get("estimated_usd_max", total["estimated_usd"])
    if lower is None:
        return "Unpriced"
    amount = f'${total["estimated_usd"]:,.2f}' if total["estimated_usd"] is not None else ""
    if lower != upper:
        bounds = f'${lower:,.2f} - ${upper:,.2f}'
        amount = f'~{amount} (range {bounds})' if amount else f'Range {bounds}'
    return amount + (" + unpriced calls" if total["unpriced_calls"] else "")


def spend_matches_display(text, expected):
    match = re.search(r"\$([\d,.]+)", text)
    if not match:
        return False
    displayed = Decimal(match.group(1).replace(",", ""))
    places = len(match.group(1).partition(".")[2])
    return abs(displayed - Decimal(str(expected))) <= Decimal(1).scaleb(-places) / 2


def spend_matches_total(text, total):
    lower = total.get("estimated_usd_min", total["estimated_usd"])
    upper = total.get("estimated_usd_max", total["estimated_usd"])
    if lower is None:
        return text == ("Unpriced" if total["calls"] else "No recorded usage")
    values = [total["estimated_usd"]] if total["estimated_usd"] is not None else []
    if lower != upper:
        values.extend((lower, upper))
    shown = re.findall(r"\$[\d,.]+", text)
    return (len(shown) == len(values)
            and all(spend_matches_display(value, expected) for value, expected in zip(shown, values))
            and ("+ unpriced calls" in text) == bool(total["unpriced_calls"]))


def table(headers, rows, first_column_links=None):
    def cell(index, value):
        text = escape(str(value))
        link = (first_column_links or {}).get(str(value)) if index == 0 else None
        return f'<td><a href="{escape(link, quote=True)}">{text}</a></td>' if link else f"<td>{text}</td>"

    return '<div class="table-scroll"><table><thead><tr>' + "".join(
        f"<th>{escape(str(h))}</th>" for h in headers
    ) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(cell(i, value) for i, value in enumerate(row)) + "</tr>" for row in rows
    ) + "</tbody></table></div>"


def payload(events, analysis):
    decisions = {d["input"]: d for d in analysis["interpretation"]["decisions"]}
    priced = {r["id"]: r for r in analysis["accounting"]["requests"]}
    humans, updates, usage, undated = [], [], [], []
    sessions = {}
    for order, event in enumerate(events):
        common = {"id": event["id"], "t": event["t"], "tool": HARNESSES[event["harness"]],
                  "session": event["session"], "source": event["source"], "order": order}
        info = sessions.setdefault((common["tool"], event["session"]), {
            "tool": common["tool"], "session": event["session"], "start": None,
            "lo": None, "hi": None, "preview": "",
        })
        if event["t"] is not None:
            info["lo"] = min(info["lo"], event["t"]) if info["lo"] is not None else event["t"]
            info["hi"] = max(info["hi"], event["t"]) if info["hi"] is not None else event["t"]
        recorded_start = analysis.get("session_starts", {}).get(event["source"].split(":")[0])
        started = (round(datetime.fromisoformat(recorded_start.replace("Z", "+00:00")).timestamp() * 1000)
                   if recorded_start else event["t"])
        if started is not None:
            info["start"] = min(info["start"], started) if info["start"] is not None else started
        if event["kind"] == "user" and not info["preview"]:
            info["preview"] = event["text"]
        if event["kind"] in ("user", "assistant"):
            row = {**common, "text": event["text"], "role": event["kind"]}
            decision = decisions.get(event["id"])
            row.update(category=review_prose(decision["category"]) if decision else "Recorded message", moment={
                "kind": review_prose(decision["category"]), "title": review_prose(decision["title"]),
                "note": review_prose(decision["analysis"]), "basis": f"Reviewed input {event['id']}.",
            } if decision else None)
            if event["t"] is None:
                undated.append(row)
            else:
                (humans if event["kind"] == "user" else updates).append(row)
        elif event["kind"] == "usage" and event["t"] is not None:
            usage.append({**priced[event["id"]], **common})
    timed = humans + updates + usage
    lo = min((e["t"] for e in timed), default=None)
    hi = max((e["t"] for e in timed), default=None)
    days, day_night, clusters = [], [], []
    if timed:
        day = datetime.fromtimestamp(lo / 1000, ZoneInfo(analysis["timezone"])).replace(hour=0, minute=0, second=0, microsecond=0)
        while day.timestamp() * 1000 <= hi:
            end = day + timedelta(days=1)
            days.append({"label": day.strftime("%a %b %d"), "date": day.date().isoformat(),
                         "start": max(lo, round(day.timestamp() * 1000)),
                         "end": min(hi, round(end.timestamp() * 1000) - 1)})
            boundaries = (day, day.replace(hour=6), day.replace(hour=18), end)
            for i in range(3):
                start = max(lo, round(boundaries[i].timestamp() * 1000))
                finish = min(hi, round(boundaries[i + 1].timestamp() * 1000))
                if start < finish:
                    day_night.append({"start": start, "end": finish, "kind": "day" if i == 1 else "night"})
            day = end
        conversations = {(h["tool"], h["session"]) for h in humans}
        for tool, session in sorted(conversations):
            pending = [h for h in humans if h["moment"] and (h["tool"], h["session"]) == (tool, session)]
            while pending:
                group = [h for h in pending if h["t"] - pending[0]["t"] <= 90 * 60000]
                pending = pending[len(group):]
                if len(group) >= 2:
                    clusters.append({"id": f"B{len(clusters)+1:03d}", "start": group[0]["t"], "end": group[-1]["t"],
                                     "inputs": [h["id"] for h in group], "count": len(group), "title": "Reviewed directions"})
    return {
        "meta": {"title": analysis["title"], "timezone": analysis["timezone"], "start": lo, "end": hi,
                 "human_count": len(humans), "model_event_count": len(usage), "assistant_update_count": len(updates),
                 "undated_count": len(undated), "curated_markers": len(decisions),
                 "method": "Navigate messages manually. Selecting an input lists all its recorded assistant responses with timestamps and sources. Selecting a response highlights it without hiding other replies. Ownership follows recorded turn IDs or matching requests before inferred same-conversation proximity. Gold marks reviewed directions. Unknown times remain outside the clock. Model events use per-request usage, not cumulative counters."},
        "sessions": sorted(sessions.values(), key=lambda row: (row["start"] is None, row["start"] or 0, row["tool"], row["session"])),
        "days": days, "day_night": day_night,
        "humans": humans, "updates": updates, "usage": usage, "undated": undated, "clusters": clusters,
        "accounting": {
            **analysis["accounting"],
            "requests": [{**r, "tool": HARNESSES[r["harness"]]} for r in analysis["accounting"]["requests"]],
            "turns": [{**r, "tool": HARNESSES[r["harness"]]} for r in analysis["accounting"]["turns"]],
        },
    }


def player_html(data):
    html = (ASSETS / "player.html").read_text(encoding="utf-8")
    substitutions = {
        "{{TITLE}}": escape(data["meta"]["title"]), "{{TIMEZONE}}": escape(data["meta"]["timezone"]),
        "{{RANGE}}": "selected interaction", "{{HUMANS}}": str(len(data["humans"])),
        "{{RESPONSES}}": str(len(data["updates"])), "{{CALLS}}": str(len(data["usage"])),
        "{{UNDATED}}": str(len(data["undated"])), "{{UNDATED_HIDDEN}}": "" if data["undated"] else "hidden",
        "/* STYLE */": (ASSETS / "player.css").read_text(encoding="utf-8"),
        "/* SCRIPT */": (ASSETS / "player.js").read_text(encoding="utf-8"),
    }
    for old, new in substitutions.items():
        html = html.replace(old, new)
    # Insert untrusted transcript data last, so it cannot collide with template tokens.
    return html.replace("/* DATA */", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))


def report_html(collection, events, analysis):
    e = escape
    by_id = {r["id"]: r for r in events}
    title = e(analysis["title"])
    period = "Unknown (no reliable timestamps)"
    if analysis["start"] is not None:
        zone = ZoneInfo(analysis["timezone"])
        start, end = [datetime.fromtimestamp(analysis[key] / 1000, zone).strftime("%Y-%m-%d %H:%M:%S %Z")
                      for key in ("start", "end")]
        period = start if analysis["start"] == analysis["end"] else f"{start} - {end}"
        if analysis["undated_events"]:
            period += "; undated records also included"
    timing = analysis["timing"]
    wall = duration(timing["wall_ms"])
    if timing["wall_ms"] is not None and timing["undated_events"]:
        wall += " (dated records only)"
    turn_time = duration(timing["total_turn_ms"])
    if timing["timed_turns"] < timing["total_turns"] and timing["total_turn_ms"] is not None:
        turn_time += " (partial)"
    html = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{REPORT_CSS}</style></head><body><main>',
            f'<header><p class="eyebrow">AGENT SESSION INSIGHTS</p><h1>{title}</h1><p>{e(review_prose(analysis["interpretation"]["summary"]))}</p><a href="player.html">Open standalone session player</a></header>',
            '<nav aria-label="Report contents">' + "".join(f'<a href="#{key}">{label}</a>' for key, label in [
                ("coverage", "Coverage"), ("usage", "Usage"), ("time", "Activity"), ("directions", "User direction"),
                ("playback", "Replay"), ("session-log", "Full session log"), ("sources", "Data")]) + "</nav>",
            '<div class="stats">' + "".join(f'<div class="stat"><b>{number(analysis[key])}</b><span>{label}</span></div>' for key, label in [
                ("human_inputs", "User inputs"), ("assistant_messages", "Assistant messages"),
                ("metered_requests", "Metered requests"), ("tool_calls", "Tool invocations")])
            + f'<div class="stat cost"><b>{e(spend(analysis["accounting"]["total"]))}</b><span>Estimated retail cost (USD)</span></div></div>'
            + f'<div class="timing-summary" aria-label="Recorded durations" title="{e(timing["method"], quote=True)}">'
            + f'<span>Wall time: <strong id="wall-time">{e(wall)}</strong></span>'
            + f'<span>Total turn time: <strong id="total-turn-time">{e(turn_time)}</strong></span>'
            + f'<small>{timing["timed_turns"]} of {timing["total_turns"]} turns timed; overlaps summed; not active compute time.</small></div>'
            + f'<p class="report-period"><b>Recorded period:</b> {e(period)}</p>',
            '<section id="coverage"><h2>Scope and coverage</h2><p>This report covers only the explicitly selected interaction. Logistics and approvals remain in the full replay; reviewed material directions are marked separately. Missing harnesses are outside the measured population.</p>']
    rows = []
    for harness, label in HARNESSES.items():
        totals = analysis["harnesses"].get(harness)
        rows.append((label, "Supplied" if totals else collection["missing_harnesses"].get(harness, "No source supplied."),
                     totals["human_inputs"] if totals else "Not measured", totals["metered_requests"] if totals else "Not measured"))
    html.append(table(["Harness", "Coverage", "Inputs", "Metered requests"], rows))
    html.append(f'<details id="coverage-details"><summary>Session details and warnings ({len(collection["sources"])} sources, {len(collection["warnings"])} warnings)</summary><div class="detail-scroll">')
    first_events = {}
    for event in events:
        source_id = event["source"].split(":")[0]
        if event["timestamp"] and (source_id not in first_events or event["timestamp"] < first_events[source_id]):
            first_events[source_id] = event["timestamp"]
    rows = []
    for source in collection["sources"]:
        started_at = source.get("started_at") or first_events.get(source["id"])
        date = (datetime.fromisoformat(started_at.replace("Z", "+00:00")).astimezone(ZoneInfo(analysis["timezone"]))
                .strftime("%Y-%m-%d %H:%M %Z") if started_at else "Unknown")
        basis = source.get("started_at_basis", "First recorded event; session start unavailable")
        rows.append((f"{date} (first recorded event)" if started_at and basis != "Session metadata" else date,
                     f'{source["id"]} · {source["session"]}',
                     f'{source["start"] or "unbounded"} to {source["end"] or "unbounded"}',
                     f'{source["raw_records"]:,}', f'{source["outside_interval"]:,}'))
    html.append(table(["Session start", "Source / session", "Selected bounds", "Raw records", "Outside scope"], rows))
    html.extend(f'<p class="source">{e(warning)}</p>' for warning in collection["warnings"])
    html.append('</div></details>')
    html.append(f'<p>Conversations: {analysis["conversations"]}. Events without reliable timestamps: {analysis["undated_events"]}. Visible message records are separate from metered requests.</p></section>')
    html.append('<section id="usage"><h2>Observed usage and model mix</h2>')
    html.append(table(["Inclusive input", "Output", "Cached reads (inside input)", "Cache creation (inside input)"],
                      [[number(analysis[key]) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")]]))
    accounting = analysis["accounting"]
    total = accounting["total"]
    model_summaries = {model: summarize([r for r in accounting["requests"] if r["model"] == model])
                       for model in total["models"]}
    token_shares_known = total["input_complete"] and total["input"] + total["output"] > 0
    cost_shares_known = (not total["unpriced_calls"] and total["estimated_usd"] is not None
                         and total["estimated_usd"] > 0
                         and total["estimated_usd_min"] == total["estimated_usd_max"])
    cost_shares_partial = (not cost_shares_known and bool(total["unpriced_calls"])
                           and total["estimated_usd"] is not None and total["estimated_usd"] > 0
                           and total["estimated_usd_min"] == total["estimated_usd_max"])

    def cost_share_of(summary):
        if not (cost_shares_known or cost_shares_partial) or summary["estimated_usd"] is None:
            return None
        return 100 * summary["estimated_usd"] / total["estimated_usd"]

    html.append(f'<p>{e(analysis["cost"])}</p><div id="model-comparison" class="model-comparison">')
    rows = []
    for model in analysis["models"]:
        summary = model_summaries[model["model"]]
        tokens = number(summary["input"] + summary["output"])
        if not summary["input_complete"]:
            tokens += "+ (incomplete)"
        share = cost_share_of(summary)
        cost_share = (f'{share:.1f}%' if share is not None
                      else "Unpriced" if cost_shares_partial else "Unavailable")
        rows.append((model["model"], number(summary["calls"]), input_number(summary), number(summary["output"]),
                     tokens, f'{model["token_percent"]:.1f}%' if token_shares_known else "Unavailable",
                     spend(summary), cost_share))
    html.append(table(["Model", "Requests", "Input tokens", "Output tokens", "Total tokens",
                       "Token share", "Estimated cost (USD)",
                       "Cost share (partial)" if cost_shares_partial else "Cost share"], rows))
    partial_note = (f' Cost shares are partial: they divide priced spend only; '
                    f'{number(total["unpriced_calls"])} unpriced requests are excluded, not counted as zero.'
                    if cost_shares_partial else '')
    html.append('</div><p class="source">Input includes cached reads and writes; total tokens = input + output. '
                'Token shares require complete, nonzero totals. Cost shares require nonzero '
                f'totals without ranges.{partial_note} Different workloads and cache use make this a usage comparison, '
                'not a model-efficiency benchmark.</p>')
    if analysis["models"]:
        cost_legend = "Cost share of priced spend (partial)" if cost_shares_partial else "Cost share"
        chart = [f'<p class="comparison-legend"><span>Token share</span><span class="cost-share">{cost_legend}</span></p>',
                 f'<svg class="chart model-chart" viewBox="0 0 900 {len(analysis["models"]) * 48}" role="img" aria-label="Token share and estimated cost share by model">']
        for i, model in enumerate(analysis["models"]):
            y = 6 + i * 48
            summary = model_summaries[model["model"]]
            chart.append(f'<text x="0" y="{y+19}" font-size="12">{e(model["model"])}</text>')
            model_cost_share = cost_share_of(summary)
            for offset, known, share, color, label in (
                (0, token_shares_known, model["token_percent"], "teal", "Token"),
                (18, model_cost_share is not None, model_cost_share or 0, "gold", "Cost"),
            ):
                if known:
                    chart.append(f'<rect x="200" y="{y+offset}" width="{share*6}" height="12" fill="var(--{color})"><title>{e(model["model"])}: {label.lower()} share {share:.1f}%</title></rect><text x="810" y="{y+offset+11}" font-size="12">{share:.1f}%</text>')
                else:
                    reason = "unpriced" if label == "Cost" and cost_shares_partial else "unavailable"
                    chart.append(f'<text x="200" y="{y+offset+11}" font-size="11">{label} share {reason}</text>')
        html.extend(chart + ["</svg>"])
    html.append('<details id="model-cost-details"><summary>Cost breakdown by model</summary>'
                '<p class="source">Each component shows priced token counts / USD. Counts cover priced requests '
                'and may include labeled cache-reuse estimates; missing prices are not zero cost.</p>')
    html.append(table(["Model", "Priced / calls", "Fresh input / USD", "Cache reads / USD",
                       "Cache writes / USD", "Output / USD", "Total USD"],
                      [(model, f'{summary["priced_calls"]} / {summary["calls"]}',
                        *(cost_component(summary, key) for key in ("fresh_input", "cache_read", "cache_write", "output")),
                        spend(summary))
                       for model, summary in model_summaries.items()]))
    html.append('</details>')
    html.append(f'<details id="turn-spend-details"><summary>Spend and tokens by user turn ({len(accounting["turns"])})</summary><div class="detail-scroll">')
    html.append(table(["Turn", "Prompt", "Models", "Calls", "Input tokens", "Output tokens", "Estimated USD"],
                      [(turn["id"], turn["text"][:60], ", ".join(turn["models"]) or "Unknown", turn["calls"],
                        input_number(turn) if turn["calls"] else "Unknown",
                        number(turn["output"]) if turn["calls"] else "Unknown", spend(turn))
                       for turn in accounting["turns"]],
                      {turn["id"]: f'player.html#{turn["id"]}' for turn in accounting["turns"]}))
    html.append(f'<p>Unattributed: {accounting["unattributed"]["calls"]} calls; {e(spend(accounting["unattributed"]))}.</p></div></details>')
    html.append('</section><section id="time"><h2>Observed activity (not measured working time)</h2>')
    html.append(table(["Bin size (minutes)", "User occupied bins", "Model occupied bins", "Shared bins", "Model bins without an input"],
                      [(b["minutes"], b["human_bins"], b["model_bins"], b["overlap_bins"], b["model_without_input_bins"]) for b in analysis["activity_bins"]]))
    html.append(f'<p>Clock timezone: {e(analysis["timezone"])}. Different bin widths show sensitivity; no claim of continuous attention, runtime, or unattended work follows from a bin.</p>')
    html.append('<details id="hourly-details"><summary>Activity by hour</summary><div class="detail-scroll">')
    html.append(table(["Local hour", "User inputs", "Model events"],
                      [(h["hour"], h["human_inputs"], h["model_events"]) for h in analysis["hourly"]]))
    html.append('</div></details>')
    html.append('</section><section id="directions"><h2 id="directions-heading">How the user shaped the work</h2><p>Reviewed interpretation, with verbatim input and response evidence. Responses establish what the historical agent reported, not independent proof of delivery.</p><div class="detail-scroll" tabindex="0" role="region" aria-labelledby="directions-heading">')
    for decision in analysis["interpretation"]["decisions"]:
        human = by_id[decision["input"]]
        html.append(f'<article class="decision"><h3>{e(review_prose(decision["title"]))}</h3><p class="source">{e(review_prose(decision["category"]))} · <a href="player.html#{human["id"]}">{human["id"]}</a> · {e(human["source"])}</p><div class="quote">{e(human["text"])}</div><p>{e(review_prose(decision["analysis"]))}</p>')
        if decision.get("response"):
            response = by_id[decision["response"]]
            html.append(f'<p class="source">Agent-reported response: <a href="player.html#{response["id"]}">{response["id"]}</a> · {e(response["source"])}</p><div class="quote agent">{e(response["text"])}</div>')
        html.append("</article>")
    html.append('</div></section><section id="playback"><h2>Interactive session playback</h2><p><a href="player.html">Open the standalone player</a>. Use the arrow buttons on the right of the session text to move between user prompts and follow their timeline positions. Drag across the timeline to zoom and use its scrollbar to pan. Search the separate full session log below. Axis labels include clock time.</p><iframe title="Session replay" class="playback-frame" src="player.html"></iframe><img class="playback-poster" src="timeline.png" alt="Static preview of the session timeline; use player.html for interaction"></section>')
    html.append('<section id="session-log"><div class="session-log-heading"><h2>Full session log</h2><input id="session-log-search" type="search" aria-label="Search full session log" placeholder="Search user/agent messages or IDs..."></div><p>Select a message to navigate Replay above. The <a href="player.html">standalone player</a> also includes the full conversation.</p><p class="source">Each user prompt includes its complete recorded turn\'s model, estimated cost and tokens. Agent rows do not repeat the charge.</p><iframe title="Full session log" class="session-log-frame" src="player.html?view=log"></iframe></section>')
    html.append('<section id="sources"><h2>Data</h2><p><a href="collection.json">Sessions</a> · <a href="analysis.json">Analysis</a> · <a href="events.jsonl">Messages and usage</a> · <a href="pricing.json">Rates</a> · <a href="verification.json">Verification</a></p>')
    html.append("""</section></main><script>
const replay=document.querySelector('.playback-frame'),log=document.querySelector('.session-log-frame');
const logSearch=document.getElementById('session-log-search');
const searchLog=()=>log.contentWindow.postMessage({type:'session-log-search',query:logSearch.value},'*');
logSearch.addEventListener('input',searchLog);log.addEventListener('load',searchLog);
window.addEventListener('message',event=>{
  if(event.source===replay.contentWindow&&event.data?.type==='session-replay-height'){
    if(Number.isFinite(event.data.height)&&event.data.height>0)replay.style.height=`${Math.ceil(event.data.height)+replay.offsetHeight-replay.clientHeight}px`;
    return;
  }
  if(event.data?.type!=='session-message-selected'||typeof event.data.id!=='string')return;
  const target=event.source===log.contentWindow?replay:event.source===replay.contentWindow?log:null;
  target?.contentWindow.postMessage({type:'session-message-selected',id:event.data.id},'*');
});
</script></body></html>""")
    return "".join(html)


def public_pdf_links(page, base_url):
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.query or url.fragment):
        raise ValueError("The public PDF base URL must be an HTTPS site directory.")
    base_url = base_url.rstrip("/") + "/"
    page.evaluate("""(base) => {
        document.querySelectorAll('a[href]').forEach(a => {
            a.href = new URL(a.getAttribute('href'), base).href;
        });
    }""", base_url)
    return base_url


def publish(output, collection, events, analysis, public_base_url=None):
    from playwright.sync_api import sync_playwright
    data = payload(events, analysis)
    html = player_html(data)
    (output / "player.html").write_text(html, encoding="utf-8")
    (output / "report.html").write_text(report_html(collection, events, analysis), encoding="utf-8")
    embedded = json.loads(re.search(r'<script id="playback-data" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert embedded == data
    assert len(data["humans"]) + len(data["updates"]) + len(data["undated"]) == analysis["human_inputs"] + analysis["assistant_messages"]
    assert len(data["usage"]) + sum(e["kind"] == "usage" and e["t"] is None for e in events) == analysis["metered_requests"]
    for source in collection["sources"]:
        assert hashlib.sha256((output / source["snapshot"]).read_bytes()).hexdigest() == source["sha256"]
    if collection.get("pricing"):
        assert hashlib.sha256((output / collection["pricing"]["snapshot"]).read_bytes()).hexdigest() == collection["pricing"]["sha256"]
    accounting = analysis["accounting"]
    for field in ("calls", "input", "output", "cached_read", "cache_write", "priced_calls", "unpriced_calls"):
        assert sum(t[field] for t in accounting["turns"]) + accounting["unattributed"][field] == accounting["total"][field], field
    for field in ("estimated_usd", "estimated_usd_min", "estimated_usd_max"):
        expected_spend = accounting["total"][field]
        if expected_spend is not None:
            assert abs(sum(t[field] or 0 for t in accounting["turns"]) +
                       (accounting["unattributed"][field] or 0) - expected_spend) < 1e-8, field
    errors, network = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append(request.url))
        page.goto((output / "player.html").as_uri(), wait_until="load")
        state = lambda: page.evaluate("window.vennPlayback.state()")
        assert page.locator("#elide-inactive").is_checked()
        dated_records = data["humans"] + data["updates"] + data["usage"]
        expected_elided = [day["date"] for day in data["days"]
                           if not any(day["start"] <= row["t"] <= day["end"] for row in dated_records)]
        assert state()["elidedDays"] == expected_elided
        assert page.locator("#session-spend").count() == 1
        assert not page.locator("#usage-details").evaluate("(el)=>el.open")
        assert page.locator(".usage-card").bounding_box()["height"] < 120

        usage_snapshot = """() => {
            const get = id => document.getElementById(id);
            const visible = id => get(id).getClientRects().length > 0 &&
                getComputedStyle(get(id)).visibility !== 'hidden';
            return {
                window:window.vennPlayback.state(), tool:get('harness').value, session:get('session').value,
                turnVisible:visible('prompt-usage'), windowVisible:visible('window-summary'),
                promptAttached:get('human-text').nextElementSibling.id === 'prompt-usage',
                totals:Object.fromEntries(['session','window','turn'].map(scope => [scope,{
                    spend:get(scope+'-spend').innerText, tokens:get(scope+'-tokens').innerText
                }]))
            };
        }"""
        turns = {turn["id"]: turn for turn in accounting["turns"]}

        def check_usage(snapshot=None):
            snapshot = snapshot if snapshot is not None else page.evaluate(usage_snapshot)
            window, tool, session = snapshot["window"], snapshot["tool"], snapshot["session"]
            selected_session = json.loads(session) if session != "all" else None
            rows = [r for r in data["accounting"]["requests"] if r["t"] is not None
                    and window["lo"] <= r["t"] <= window["hi"] and (tool == "all" or r["tool"] == tool)
                    and (selected_session is None or [r["tool"], r["session"]] == selected_session)]
            scopes = [("session", accounting["total"]), ("window", summarize(rows))]
            turn = turns.get(window["selected"])
            if turn:
                scopes.append(("turn", turn))
                assert snapshot["turnVisible"]
                assert snapshot["promptAttached"]
            filtered = data["meta"]["start"] is not None and (
                window["lo"] != data["meta"]["start"] or window["hi"] != data["meta"]["end"]
                or tool != "all" or session != "all" or any(r["t"] is None for r in data["accounting"]["requests"])
            )
            assert snapshot["windowVisible"] == filtered
            for scope, total in scopes:
                shown = snapshot["totals"][scope]["spend"]
                assert spend_matches_total(shown, total), (scope, shown)
                if total["calls"]:
                    assert snapshot["totals"][scope]["tokens"] == (
                        f'{input_number(total)} in · {total["output"]:,} out · {total["calls"]:,} calls'
                    )

        def filter_snapshots(control, values):
            return page.evaluate("""({control,values}) => {
                const snapshot = """ + usage_snapshot + """;
                const select = document.getElementById(control);
                return values.map(value => {
                    select.value = value;
                    if (select.value !== value) throw new Error('Missing filter option: '+value);
                    select.dispatchEvent(new Event('input',{bubbles:true}));
                    select.dispatchEvent(new Event('change',{bubbles:true}));
                    return snapshot();
                });
            }""", {"control": control, "values": values})

        check_usage()
        for row in page.locator("#event-list .event-row").evaluate_all(
            "(rows)=>rows.map(el=>({id:el.dataset.id,usage:el.querySelector('.event-usage')?.textContent??null}))"
        ):
            turn = turns.get(row["id"])
            if not turn:
                assert row["usage"] is None
            else:
                assert row["usage"] is not None
                assert (", ".join(turn["models"]) or "Model unknown") in row["usage"]
                if turn["calls"]:
                    assert f'{input_number(turn)} in · {turn["output"]:,} out' in row["usage"]
                if turn["estimated_usd_min"] is not None:
                    assert spend_matches_total(row["usage"], turn), (row["id"], row["usage"])
                else:
                    assert ("Unpriced" if turn["calls"] else "No recorded usage") in row["usage"]
        navigation_errors = page.evaluate("""({rows, turns}) => {
            const errors = [];
            for (const row of rows) {
                window.vennPlayback.jumpToMessage(row.id);
                const node = row.role === 'user' ? document.getElementById('human-text')
                    : document.querySelector(`#agent-text [data-id="${row.id}"] .response-text`);
                if (node?.textContent !== row.text) errors.push(row.id);
                if (row.t === null && document.getElementById('undated-text').textContent !== row.text) errors.push(row.id + ':undated');
                if (row.role === 'user') {
                    const shown = [...document.querySelectorAll('#agent-text .agent-response')].map(el => el.dataset.id);
                    if (JSON.stringify(shown) !== JSON.stringify(turns[row.id] || [])) errors.push(row.id + ':responses');
                }
            }
            return errors;
        }""", {"rows": sorted(data["humans"] + data["updates"], key=lambda r: (r["t"], r["order"])) + data["undated"],
               "turns": {turn["id"]: turn["response_ids"] for turn in analysis["accounting"]["turns"]}})
        assert not navigation_errors, navigation_errors
        if data["meta"]["start"] is not None:
            page.locator("#start").click()
            if len(data["humans"]) > 1:
                prompts = sorted(data["humans"], key=lambda row: (row["t"], row["order"]))
                page.evaluate("(id) => window.vennPlayback.jumpToMessage(id)", prompts[0]["id"])
                page.locator("#next-text").click()
                assert state()["message"] == prompts[1]["id"]
                page.locator("#prev-text").click()
                assert state()["message"] == prompts[0]["id"]
                page.locator("#start").click()
            width = state()["hi"] - state()["lo"]
            if width > 1000:
                page.locator("#zoom-in").click()
                assert state()["hi"] - state()["lo"] < width
                assert all(re.search(r"\d{2}:\d{2}", v) for v in page.locator(".axis-label").all_text_contents())
                page.locator("#pan-next").click()
                assert state()["lo"] > data["meta"]["start"]
                page.locator("#pan-prev").click()
                page.locator("#zoom-out").click()
                page.locator("#zoom-in").click()
                page.locator("#zoom-reset").click()
                assert state()["hi"] - state()["lo"] == width
                page.locator("#timeline").scroll_into_view_if_needed()
                box = page.locator("#timeline").bounding_box()
                width_svg = page.locator("#timeline").evaluate("(el) => el.viewBox.baseVal.width")
                left = box["x"] + (150 + .2 * (width_svg - 170)) / width_svg * box["width"]
                right = box["x"] + (150 + .6 * (width_svg - 170)) / width_svg * box["width"]
                y = box["y"] + box["height"] * .55
                page.mouse.move(left, y)
                page.mouse.down()
                page.mouse.move(right, y, steps=6)
                page.mouse.up()
                assert state()["hi"] - state()["lo"] < width
                check_usage()
                bar = page.locator("#timeline-scroll")
                assert bar.evaluate("(el)=>el.scrollWidth>el.clientWidth")
                assert bar.evaluate("(el)=>el.offsetHeight>el.clientHeight")
                bar.evaluate("(el)=>{el.scrollLeft=el.scrollWidth-el.clientWidth;}")
                page.wait_for_function("Math.abs(window.vennPlayback.state().hi-window.vennPlayback.state().scopeHi)<1")
                check_usage()
                page.locator("#zoom-reset").click()
                check_usage()
                if width > 60000:
                    page.locator("#start").click()
                    before = state()["now"]
                    page.locator("#scrub").evaluate('(el)=>{el.value=50000;el.dispatchEvent(new Event("input",{bubbles:true}));}')
                    assert state()["now"] > before
            page.locator("#end").click()
            assert state()["visibleHumanInputs"] == len(data["humans"])
            assert state()["visibleModelEvents"] == len(data["usage"])
            tools = sorted({row["tool"] for row in data["humans"] + data["updates"] + data["usage"]})
            for tool, snapshot in zip(tools, filter_snapshots("harness", tools), strict=True):
                assert snapshot["tool"] == tool
                assert snapshot["window"]["visibleHumanInputs"] == sum(row["tool"] == tool for row in data["humans"])
                check_usage(snapshot)
            page.locator("#harness").select_option("all")
            session_values = [json.dumps([session["tool"], session["session"]], separators=(",", ":"))
                              for session in data["sessions"]]
            for session, value, snapshot in zip(data["sessions"], session_values,
                                                 filter_snapshots("session", session_values), strict=True):
                assert snapshot["session"] == value
                assert snapshot["window"]["visibleHumanInputs"] == sum(row["tool"] == session["tool"] and row["session"] == session["session"] for row in data["humans"])
                check_usage(snapshot)
            page.locator("#session").select_option("all")
            for day, snapshot in zip(data["days"], filter_snapshots("day", [day["date"] for day in data["days"]]), strict=True):
                assert snapshot["window"]["visibleHumanInputs"] == sum(day["start"] <= row["t"] <= day["end"] for row in data["humans"])
                check_usage(snapshot)
            page.locator("#day").select_option("all")
            page.locator("#elide-inactive").uncheck()
            page.locator("#scrub").evaluate('(el)=>{el.value=50000;el.dispatchEvent(new Event("input",{bubbles:true}));}')
            assert abs(state()["now"] - (data["meta"]["start"] + data["meta"]["end"]) / 2) < 1
            page.locator("#elide-inactive").check()
            page.locator("#start").click()
        else:
            assert page.locator("#clock").inner_text() == "Timing unavailable"
        search_row = (data["humans"] + data["updates"] + data["undated"])[0] if data["humans"] + data["updates"] + data["undated"] else None
        if search_row:
            page.locator("#search").fill(search_row["id"])
            assert page.locator(f'#event-list [data-id="{search_row["id"]}"]').count() == 1
            page.locator("#search").fill("")
        page.locator("#timeline").screenshot(path=str(output / "timeline.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.goto((output / "report.html").as_uri(), wait_until="load")
        assert page.locator("h1").count() == 1
        assert page.locator(".record, #record, #limits").count() == 0
        assert page.locator('iframe[src="player.html"]').count() == 1
        assert page.locator('#session-log > iframe[src="player.html?view=log"]').count() == 1
        assert page.locator("img").evaluate_all("(images)=>images.every(i=>i.complete&&i.naturalWidth>0)")
        page.evaluate("document.querySelectorAll('#coverage-details, #turn-spend-details, #hourly-details').forEach(el=>el.open=true)")
        if public_base_url:
            public_base_url = public_pdf_links(page, public_base_url)
        page.pdf(path=str(output / "report.pdf"), format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        assert not [url for url in network if url.startswith(("http://", "https://"))], network
        browser.close()
    pdf = (output / "report.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
    if public_base_url:
        links = re.findall(rb"/URI\s*\(([^)]*)\)", pdf)
        assert any(link.startswith(public_base_url.encode()) for link in links)
        assert not any(link.lower().startswith(b"file:") for link in links)
    result = {
        "status": "passed", "source_hashes": "passed", "embedded_data": "passed",
        "all_visible_messages": analysis["human_inputs"] + analysis["assistant_messages"],
        "human_inputs": analysis["human_inputs"], "assistant_messages": analysis["assistant_messages"],
        "metered_requests": analysis["metered_requests"], "message_navigation": "passed",
        "usage_cost_and_turn_reconciliation": "passed",
        "usage_window_scroll_zoom_and_filters": "passed",
        "compact_totals_and_prompt_attached_usage": "passed",
        "full_log_prompt_usage": "passed",
        "estimated_usd": accounting["total"]["estimated_usd"],
        "estimated_usd_min": accounting["total"]["estimated_usd_min"],
        "estimated_usd_max": accounting["total"]["estimated_usd_max"],
        "priced_requests": accounting["total"]["priced_calls"],
        "unpriced_requests": accounting["total"]["unpriced_calls"],
        "unattributed_requests": accounting["unattributed"]["calls"],
        "zoom_pan_reset_and_manual_navigation": "passed" if data["meta"]["start"] is not None else "not applicable: undated",
        "drag_zoom_and_native_scrollbar": "passed" if data["meta"]["start"] is not None and data["meta"]["end"] - data["meta"]["start"] > 1000 else "not applicable",
        "harness_day_filters_and_scrubbing": "passed" if data["meta"]["start"] is not None else "not applicable: undated",
        "desktop_mobile_and_pdf": "passed", "external_network_requests": 0, "browser_errors": errors,
        "pdf_bytes": len(pdf),
        "pdf_link_base_url": public_base_url,
    }
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for path in output.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
