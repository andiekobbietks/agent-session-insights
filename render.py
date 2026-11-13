"""Venn-style offline reports, standalone playback, PDF, and browser validation."""
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
*{box-sizing:border-box}body{font:15px/1.6 system-ui,sans-serif;color:var(--ink);background:#f3f6f8;margin:0;overflow-wrap:anywhere}
main{max-width:1120px;margin:32px auto;padding:0 28px}header{background:var(--navy);color:#ecf4f8;border-radius:14px;padding:28px}
h1{font-size:34px;line-height:1.15;letter-spacing:-.03em;margin:8px 0 16px}h2{font-size:22px;border-bottom:1px solid #dde5eb;padding-bottom:8px;margin:28px 0 16px}
h3{font-size:17px;margin:8px 0}.eyebrow{font-size:11px;font-weight:750;letter-spacing:.16em}.muted{color:var(--muted)}
section{background:white;border:1px solid #dde5eb;border-radius:10px;padding:24px;margin:20px 0}a{color:#1f6b83}header a{color:#ffd58c}
nav{display:flex;flex-wrap:wrap;gap:16px;margin:20px 0;font-size:13px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}
.stat{background:white;border:1px solid #dde5eb;border-radius:10px;padding:18px}.stat b{display:block;color:var(--teal);font-size:27px}.stat span{font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:9px;border-bottom:1px solid #dde5eb;text-align:left;vertical-align:top}th{background:#edf3f6}.table-scroll{overflow:auto}
.decision{border-left:3px solid var(--gold);padding:4px 16px;margin:24px 0}.quote,.message{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 system-ui,sans-serif;background:#f5f8fa;padding:14px;border-left:3px solid var(--teal)}
.agent{border-left-color:#839daf}.source{font-size:11px;color:var(--muted)}.playback-frame{width:100%;height:1250px;border:1px solid #dde5eb;border-radius:10px}
.record-usage{font-size:11px;color:var(--teal);margin:0 0 12px;padding:8px 14px;background:#edf6f4;border-left:3px solid var(--teal)}
.playback-poster{display:none;width:100%}.chart{width:100%;max-height:210px}details{margin:18px 0}summary{cursor:pointer;font-weight:650}code{font-size:.85em}
@media(max-width:600px){main{padding:0 14px;margin:16px auto}h1{font-size:27px}section{padding:16px}.stats{grid-template-columns:repeat(2,1fr)}header{padding:20px}}
@media print{body{background:white;font-size:10pt}main{padding:0;margin:0;max-width:none}nav,.playback-frame{display:none}.playback-poster{display:block}
section{border:0;padding:0;margin:20px 0}header{background:#edf3f6;color:var(--ink);padding:18px}.stats{grid-template-columns:repeat(4,1fr)}.stat{padding:10px}
h2,h3,summary{break-after:avoid}.decision,.stat,tr{break-inside:avoid}.quote,.message{font-size:9pt}details{break-inside:auto}a{color:inherit;text-decoration:none}}
"""


def number(value):
    return f"{value:,}" if value is not None else "Unknown"


def spend(total):
    if not total["calls"]:
        return "Unknown (no recorded usage)"
    if total["estimated_usd"] is None:
        return "Unpriced"
    return f'${total["estimated_usd"]:,.6f}' + (" + unpriced calls" if total["unpriced_calls"] else "")


def spend_matches_display(text, expected):
    match = re.search(r"\$([\d,.]+)", text)
    if not match:
        return False
    displayed = Decimal(match.group(1).replace(",", ""))
    return abs(displayed - Decimal(str(expected))) <= Decimal("0.0000005")


def table(headers, rows):
    return '<div class="table-scroll"><table><thead><tr>' + "".join(
        f"<th>{escape(str(h))}</th>" for h in headers
    ) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>" for row in rows
    ) + "</tbody></table></div>"


def payload(events, analysis):
    decisions = {d["input"]: d for d in analysis["interpretation"]["decisions"]}
    priced = {r["id"]: r for r in analysis["accounting"]["requests"]}
    humans, updates, usage, undated = [], [], [], []
    for order, event in enumerate(events):
        common = {"id": event["id"], "t": event["t"], "tool": HARNESSES[event["harness"]],
                  "session": event["session"], "source": event["source"], "order": order}
        if event["kind"] in ("user", "assistant"):
            row = {**common, "text": event["text"], "role": event["kind"]}
            decision = decisions.get(event["id"])
            row.update(category=decision["category"] if decision else "Recorded message", moment={
                "kind": decision["category"], "title": decision["title"],
                "note": decision["analysis"], "basis": f"Reviewed input {event['id']}.",
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
    days, clusters = [], []
    if timed:
        day = datetime.fromtimestamp(lo / 1000, ZoneInfo(analysis["timezone"])).replace(hour=0, minute=0, second=0, microsecond=0)
        while day.timestamp() * 1000 <= hi:
            end = day + timedelta(days=1)
            days.append({"label": day.strftime("%a %b %d"), "date": day.date().isoformat(),
                         "start": max(lo, round(day.timestamp() * 1000)),
                         "end": min(hi, round(end.timestamp() * 1000) - 1)})
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
                 "method": "All genuine messages in the explicit scope are retained. Gold marks reviewed material directions, not a creativity score. Unknown times remain outside the clock. Tool payloads and internal reasoning are not displayed. Model events use authoritative per-request accounting, not cumulative counters. Agent claims in historical text are not independently verified outcomes."},
        "days": days, "humans": humans, "updates": updates, "usage": usage, "undated": undated, "clusters": clusters,
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
    html = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{REPORT_CSS}</style></head><body><main>',
            f'<header><p class="eyebrow">AGENT SESSION INSIGHTS / VENN DESIGN</p><h1>{title}</h1><p>{e(analysis["interpretation"]["summary"])}</p><a href="player.html">Open standalone session player</a></header>',
            '<nav aria-label="Report contents">' + "".join(f'<a href="#{key}">{label}</a>' for key, label in [
                ("coverage", "Coverage"), ("usage", "Usage"), ("time", "Activity"), ("directions", "Human direction"),
                ("playback", "Replay"), ("limits", "Limits"), ("record", "Conversation"), ("sources", "Sources")]) + "</nav>",
            '<div class="stats">' + "".join(f'<div class="stat"><b>{number(analysis[key])}</b><span>{label}</span></div>' for key, label in [
                ("human_inputs", "Human inputs"), ("assistant_messages", "Assistant messages"),
                ("metered_requests", "Metered requests"), ("tool_calls", "Tool invocations")]) + "</div>",
            '<section id="coverage"><h2>Scope and coverage</h2><p>This report covers only the explicitly selected interaction. Logistics and approvals remain in the full replay; reviewed material directions are marked separately. Missing harnesses are outside the measured population.</p>']
    rows = []
    for harness, label in HARNESSES.items():
        totals = analysis["harnesses"].get(harness)
        rows.append((label, "Supplied" if totals else collection["missing_harnesses"].get(harness, "No source supplied."),
                     totals["human_inputs"] if totals else "Not measured", totals["metered_requests"] if totals else "Not measured"))
    html.append(table(["Harness", "Coverage", "Inputs", "Metered requests"], rows))
    for source in collection["sources"]:
        html.append(f'<p class="source">{e(source["id"])} · {e(source["session"])} · bounds: {e(source["start"] or "unbounded")} to {e(source["end"] or "unbounded")} · {source["raw_records"]:,} raw records inspected; {source["outside_interval"]:,} supported events outside scope.</p>')
    html.extend(f'<p class="source">{e(warning)}</p>' for warning in collection["warnings"])
    html.append(f'<p>Conversations: {analysis["conversations"]}. Events without reliable timestamps: {analysis["undated_events"]}. Visible message records are separate from metered requests.</p></section>')
    html.append('<section id="usage"><h2>Observed usage and model mix</h2>')
    html.append(table(["Inclusive input", "Output", "Cached reads (inside input)", "Cache creation (inside input)"],
                      [[number(analysis[key]) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")]]))
    html.append(table(["Model", "Requests", "Input tokens", "Output tokens", "Share of observed tokens"],
                      [(m["model"], number(m["requests"]), number(m["input"]), number(m["output"]), f'{m["token_percent"]:.2f}%') for m in analysis["models"]]))
    if analysis["models"]:
        chart = ['<svg class="chart" viewBox="0 0 900 160" role="img" aria-label="Share of observed tokens by model">']
        for i, model in enumerate(analysis["models"]):
            y = 8 + i * min(28, 140 / len(analysis["models"]))
            chart.append(f'<text x="0" y="{y+14}" font-size="12">{e(model["model"])}</text><rect x="200" y="{y}" width="{model["token_percent"]*6}" height="16" fill="#2c8582"/><text x="810" y="{y+14}" font-size="12">{model["token_percent"]:.1f}%</text>')
        html.extend(chart + ["</svg>"])
    accounting = analysis["accounting"]
    html.append(f'<p>{e(analysis["cost"])}</p>')
    html.append(table(["Model", "Priced / observed calls", "Estimated USD"],
                      [(model, f'{summary["priced_calls"]} / {summary["calls"]}', spend(summary))
                       for model in accounting["total"]["models"]
                       for summary in [summarize([r for r in accounting["requests"] if r["model"] == model])]]))
    html.append('<h3>Spend and tokens by human turn</h3><p>Complete recorded turns, including logistics. Recorded turn IDs take precedence; other ownership is inferred from response links or preceding inputs in the same conversation. Missing or unmatched inputs remain unattributed. Zero metered calls means unknown usage, not zero actual spend.</p>')
    html.append(table(["Turn", "Models", "Calls", "Input tokens", "Output tokens", "Estimated USD"],
                      [(turn["id"], ", ".join(turn["models"]) or "Unknown", turn["calls"],
                        number(turn["input"]) if turn["calls"] else "Unknown",
                        number(turn["output"]) if turn["calls"] else "Unknown", spend(turn))
                       for turn in accounting["turns"]]))
    html.append(f'<p>Unattributed: {accounting["unattributed"]["calls"]} calls; {e(spend(accounting["unattributed"]))}. Per-request ownership methods are included in analysis.json and the standalone player.</p>')
    html.append(f'<p>{e(accounting["pricing"]["method"])}</p>')
    if collection.get("pricing"):
        html.append(f'<p>Frozen pricing: <a href="pricing.json">rates, dates, sources and assumptions</a>. SHA-256: <code>{e(collection["pricing"]["sha256"])}</code>. {e(accounting["pricing"]["verified_on_by_model_note"])}</p>')
    html.extend(f'<p class="source">{e(value)}</p>' for value in accounting["pricing"]["assumptions"])
    html.append('</section><section id="time"><h2>Observed activity, not measured working time</h2>')
    html.append(table(["Bin size (minutes)", "Human occupied bins", "Model occupied bins", "Shared bins", "Model bins without an input"],
                      [(b["minutes"], b["human_bins"], b["model_bins"], b["overlap_bins"], b["model_without_input_bins"]) for b in analysis["activity_bins"]]))
    html.append(f'<p>Clock timezone: {e(analysis["timezone"])}. Different bin widths show sensitivity; no claim of continuous attention, runtime, or unattended work follows from a bin.</p>')
    html.append(table(["Local hour", "Human inputs", "Model events"],
                      [(h["hour"], h["human_inputs"], h["model_events"]) for h in analysis["hourly"]]))
    html.append('</section><section id="directions"><h2>How human direction shaped the work</h2><p>Reviewed interpretation, with verbatim input and response evidence. Responses establish what the historical agent reported, not independent proof of delivery.</p>')
    for decision in analysis["interpretation"]["decisions"]:
        human = by_id[decision["input"]]
        html.append(f'<article class="decision"><h3>{e(decision["title"])}</h3><p class="source">{e(decision["category"])} · <a href="player.html#{human["id"]}">{human["id"]}</a> · {e(human["source"])}</p><div class="quote">{e(human["text"])}</div><p>{e(decision["analysis"])}</p>')
        if decision.get("response"):
            response = by_id[decision["response"]]
            html.append(f'<p class="source">Agent-reported response: <a href="player.html#{response["id"]}">{response["id"]}</a> · {e(response["source"])}</p><div class="quote agent">{e(response["text"])}</div>')
        html.append("</article>")
    html.append('</section><section id="playback"><h2>Interactive session playback</h2><p><a href="player.html">Open the standalone player</a>. Use the arrow buttons on the right of the session text to move between messages. Drag across the timeline to zoom and use its scrollbar to pan, or search the full conversation. Axis labels include clock time.</p><iframe title="Session replay" class="playback-frame" src="player.html"></iframe><img class="playback-poster" src="timeline.png" alt="Static preview of the session timeline; use player.html for interaction"></section>')
    html.append('<section id="limits"><h2>Evidence limits and method</h2><ul>' + "".join(f"<li>{e(value)}</li>" for value in analysis["limitations"]) + '</ul><p>Raw files are frozen privately with SHA-256 hashes. Canonical message records and authoritative request IDs prevent mirror/cumulative-token double counting. The new report/player are browser-checked; this does not retroactively verify claims in the historical conversation.</p></section>')
    html.append('<section id="record"><h2>Complete visible conversation</h2><p>All selected user and assistant messages, not just material prompts. Tool payloads and internal reasoning are intentionally excluded.</p>')
    turns = {turn["id"]: turn for turn in accounting["turns"]}
    for event in events:
        if event["kind"] not in ("user", "assistant"):
            continue
        label = "Human" if event["kind"] == "user" else "Agent"
        html.append(f'<details id="{event["id"]}" class="record"><summary>{event["id"]} · {label} · {e(event["timestamp"] or "Undated")}</summary><p class="source">{e(event["source"])} · <a href="player.html#{event["id"]}">Replay this message</a></p><div class="message {"agent" if label == "Agent" else ""}">{e(event["text"])}</div>')
        turn = turns.get(event["id"])
        if turn:
            tokens = f'{turn["input"]:,} in · {turn["output"]:,} out · {turn["calls"]:,} calls' if turn["calls"] else "Tokens unavailable"
            html.append(f'<p class="record-usage">{e(", ".join(turn["models"]) or "Model unknown")} · Estimated {e(spend(turn))} · {tokens} · Complete recorded turn</p>')
        html.append('</details>')
    html.append('</section><section id="sources"><h2>Sources and reproducibility</h2><p><a href="collection.json">Source manifest and hashes</a> · <a href="analysis.json">Analysis data</a> · <a href="events.jsonl">Normalized evidence</a> · <a href="verification.json">Verification result</a></p>')
    for source in collection["sources"]:
        html.append(f'<p class="source">{e(source["id"])}: {e(source["original_path"])}<br>SHA-256: {source["sha256"]}</p>')
    html.append('</section></main></body></html>')
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
    expected_spend = accounting["total"]["estimated_usd"]
    if expected_spend is not None:
        assert abs(sum(t["estimated_usd"] or 0 for t in accounting["turns"]) +
                   (accounting["unattributed"]["estimated_usd"] or 0) - expected_spend) < 1e-8
    errors, network = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append(request.url))
        page.goto((output / "player.html").as_uri(), wait_until="networkidle")
        state = lambda: page.evaluate("window.vennPlayback.state()")
        assert page.locator("#session-spend").count() == 1
        assert not page.locator("#usage-details").evaluate("(el)=>el.open")
        assert page.locator(".usage-card").bounding_box()["height"] < 120

        def check_usage():
            window = state()
            tool = page.locator("#harness").input_value()
            rows = [r for r in data["accounting"]["requests"] if r["t"] is not None
                    and window["lo"] <= r["t"] <= window["hi"] and (tool == "all" or r["tool"] == tool)]
            scopes = [("session", accounting["total"]), ("window", summarize(rows))]
            turn = next((t for t in accounting["turns"] if t["id"] == window["selected"]), None)
            if turn:
                scopes.append(("turn", turn))
                assert page.locator("#prompt-usage").is_visible()
                assert page.evaluate("document.getElementById('human-text').nextElementSibling.id==='prompt-usage'")
            filtered = data["meta"]["start"] is not None and (
                window["lo"] != data["meta"]["start"] or window["hi"] != data["meta"]["end"]
                or tool != "all" or any(r["t"] is None for r in data["accounting"]["requests"])
            )
            assert page.locator("#window-summary").is_visible() == filtered
            for scope, total in scopes:
                shown = page.locator(f"#{scope}-spend").inner_text()
                if total["estimated_usd"] is None:
                    assert shown == ("Unpriced" if total["calls"] else "No recorded usage"), (scope, shown)
                else:
                    assert spend_matches_display(shown, total["estimated_usd"]), (scope, shown)
                    assert ("+ unpriced calls" in shown) == bool(total["unpriced_calls"])
                if total["calls"]:
                    assert page.locator(f"#{scope}-tokens").inner_text() == (
                        f'{total["input"]:,} in · {total["output"]:,} out · {total["calls"]:,} calls'
                    )

        check_usage()
        turns = {turn["id"]: turn for turn in accounting["turns"]}
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
                    assert f'{turn["input"]:,} in · {turn["output"]:,} out' in row["usage"]
                if turn["estimated_usd"] is not None:
                    assert spend_matches_display(row["usage"], turn["estimated_usd"]), (row["id"], row["usage"])
                else:
                    assert ("Unpriced" if turn["calls"] else "No recorded usage") in row["usage"]
        for row in sorted(data["humans"] + data["updates"], key=lambda r: (r["t"], r["order"])):
            page.evaluate("(id)=>window.vennPlayback.jumpToMessage(id)", row["id"])
            selector = "#human-text" if row["role"] == "user" else "#agent-text"
            assert page.locator(selector).inner_text() == row["text"], row["id"]
        for row in data["undated"]:
            page.evaluate("(id)=>window.vennPlayback.jumpToMessage(id)", row["id"])
            assert page.locator("#undated-text").inner_text() == row["text"]
        if data["meta"]["start"] is not None:
            page.locator("#start").click()
            first = state()["message"]
            if len(data["humans"]) + len(data["updates"]) > 1:
                page.locator("#next-text").click()
                assert state()["message"] != first
                page.locator("#prev-text").click()
                assert state()["message"] == first
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
                left = box["x"] + (150 + .2 * 930) / 1100 * box["width"]
                right = box["x"] + (150 + .6 * 930) / 1100 * box["width"]
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
                    page.locator("#skip").uncheck()
                    page.locator("#speed").select_option("5")
                    before = state()["now"]
                    page.locator("#play").click()
                    page.wait_for_timeout(80)
                    page.locator("#play").click()
                    assert state()["now"] > before and not state()["playing"]
            page.locator("#end").click()
            assert int(page.locator("#human-count").inner_text().replace(",", "")) == len(data["humans"])
            assert int(page.locator("#agent-count").inner_text().replace(",", "")) == len(data["usage"])
            for tool in {row["tool"] for row in data["humans"] + data["updates"] + data["usage"]}:
                page.locator("#harness").select_option(tool)
                assert state()["visibleHumanInputs"] == sum(row["tool"] == tool for row in data["humans"])
                check_usage()
            page.locator("#harness").select_option("all")
            for day in data["days"]:
                page.locator("#day").select_option(day["date"])
                assert state()["visibleHumanInputs"] == sum(day["start"] <= row["t"] <= day["end"] for row in data["humans"])
                check_usage()
            page.locator("#day").select_option("all")
            page.locator("#scrub").evaluate('(el)=>{el.value=50000;el.dispatchEvent(new Event("input",{bubbles:true}));}')
            assert abs(state()["now"] - (data["meta"]["start"] + data["meta"]["end"]) / 2) < 1
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
        page.goto((output / "report.html").as_uri(), wait_until="networkidle")
        assert page.locator("h1").count() == 1
        assert page.locator(".record").count() == analysis["human_inputs"] + analysis["assistant_messages"]
        assert page.locator(".record .record-usage").count() == analysis["human_inputs"]
        assert page.locator("img").evaluate_all("(images)=>images.every(i=>i.complete&&i.naturalWidth>0)")
        page.evaluate("document.querySelectorAll('details.record').forEach(el=>el.open=true)")
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
        "priced_requests": accounting["total"]["priced_calls"],
        "unpriced_requests": accounting["total"]["unpriced_calls"],
        "unattributed_requests": accounting["unattributed"]["calls"],
        "zoom_pan_reset_and_clock_ticks": "passed" if data["meta"]["start"] is not None else "not applicable: undated",
        "drag_zoom_and_native_scrollbar": "passed" if data["meta"]["start"] is not None and data["meta"]["end"] - data["meta"]["start"] > 1000 else "not applicable",
        "harness_day_filters_and_scrubbing": "passed" if data["meta"]["start"] is not None else "not applicable: undated",
        "desktop_mobile_and_pdf": "passed", "external_network_requests": 0, "browser_errors": errors,
        "pdf_bytes": len(pdf),
        "pdf_link_base_url": public_base_url,
    }
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for path in output.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
