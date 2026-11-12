#!/usr/bin/env python3
"""Local collection, analysis, and publishing entry point."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from accounting import account, validate_rates
from sources import HARNESSES, epoch, parse, redact, timestamp


def write(path, value):
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def write_json(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(manifest_path, output):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("sources"), list) or not manifest["sources"]:
        raise ValueError("Manifest needs at least one explicitly selected source.")
    ZoneInfo(manifest.get("timezone", "America/Los_Angeles"))
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output directory must be empty; existing results will not be overwritten.")
    output.chmod(0o700)
    snapshots = output / "private" / "sources"
    snapshots.mkdir(parents=True, mode=0o700)
    collection = {"title": redact(manifest.get("title", "Session review")),
                  "timezone": manifest.get("timezone", "America/Los_Angeles"),
                  "sources": [], "missing_harnesses": manifest.get("missing_harnesses", {}),
                  "warnings": []}
    if manifest.get("pricing"):
        pricing_path = (manifest_path.parent / manifest["pricing"]).resolve()
        pricing_data = pricing_path.read_bytes()
        pricing = json.loads(pricing_data)
        validate_rates(pricing)
        write(output / "pricing.json", pricing_data.decode("utf-8"))
        collection["pricing"] = {
            "snapshot": "pricing.json", "sha256": hashlib.sha256(pricing_data).hexdigest(),
            "original_path": str(pricing_path), "card": pricing,
        }
    events, unique = [], {}
    for index, spec in enumerate(manifest["sources"], 1):
        if spec.get("harness") not in HARNESSES or not spec.get("session"):
            raise ValueError("Each source needs a supported harness and a session identifier.")
        source_id = f"S{index:02d}"
        original = (manifest_path.parent / spec["path"]).resolve()
        if not original.is_file():
            raise ValueError(f"Selected source is not an accessible file: {original}")
        before = original.stat()
        data = original.read_bytes()
        after = original.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Source changed while being copied: {original}; retry with a frozen copy.")
        snapshot = snapshots / f"{source_id}{original.suffix}"
        snapshot.write_bytes(data)
        snapshot.chmod(0o600)
        digest = hashlib.sha256(data).hexdigest()
        start, end = timestamp(spec.get("start")), timestamp(spec.get("end"))
        if start and end and start > end:
            raise ValueError("Source start must not follow end.")
        parse_spec = {**spec, "subagent": spec.get("subagent", False) or "subagents" in original.parts}
        parsed, warnings, raw_count = parse(snapshot, parse_spec, source_id)
        scoped = []
        outside = 0
        for event in parsed:
            time = event["timestamp"]
            if time is None and (start or end):
                raise ValueError(f"{event['source']}: undated event cannot be assigned to an explicit time interval.")
            if (start and time < start) or (end and time > end):
                outside += 1
                continue
            key = (event["harness"], event["session"], event["kind"], event["identity"])
            if key in unique:
                previous = unique[key]
                ignored = {"source", "timestamp"}
                if event["harness"] == "claude" and event["kind"] == "usage":
                    for field in ("input", "output", "cached_read", "cache_write"):
                        previous[field] = max(previous[field], event[field])
                elif any(previous.get(k) != v for k, v in event.items() if k not in ignored):
                    warnings.append(f"Conflicting duplicate at {event['source']}; first record retained.")
                continue
            unique[key] = event
            events.append(event)
            scoped.append(event)
        counts = dict(Counter(e["kind"] for e in scoped))
        if not scoped:
            warnings.append("No new supported events inside the selected scope (possibly duplicate sources).")
        if not counts.get("usage"):
            warnings.append("No authoritative per-request usage observed; token totals for this source are unknown.")
        undated = sum(e["timestamp"] is None for e in scoped)
        if undated:
            warnings.append(f"{undated} events lack reliable timestamps and remain outside the clock.")
        if spec["harness"] == "copilot":
            warnings.append("Event-log-only import: SQL-only and unrecorded auxiliary requests are not reconstructed.")
        collection["sources"].append({
            "id": source_id, "harness": spec["harness"], "session": spec["session"],
            "original_path": str(original), "snapshot": str(snapshot.relative_to(output)),
            "sha256": digest, "bytes": len(data), "raw_records": raw_count,
            "start": start, "end": end, "outside_interval": outside, "counts": counts,
            "warnings": warnings,
        })
    if not events:
        raise ValueError("No supported events collected; check the selected export format and scope.")
    order = {f"S{i:02d}": i for i in range(1, len(collection["sources"]) + 1)}
    events.sort(key=lambda e: (
        e["timestamp"] is None, e["timestamp"] or "",
        order[e["source"].split(":")[0]], int(e["source"].split(":")[1]),
    ))
    counts = Counter()
    prefixes = {"user": "H", "assistant": "R", "usage": "A", "tool": "T"}
    for event in events:
        counts[event["kind"]] += 1
        event["id"] = f"{prefixes[event['kind']]}{counts[event['kind']]:03d}"
        event["t"] = epoch(event["timestamp"])
        if event["kind"] == "usage" and event["cached_read"] + event["cache_write"] > event["input"]:
            raise ValueError(f"Inconsistent merged token accounting at {event['source']}")
    supplied = {s["harness"] for s in collection["sources"]}
    for harness in HARNESSES.keys() - supplied:
        collection["missing_harnesses"].setdefault(harness, "No source supplied.")
    for source in collection["sources"]:
        collection["warnings"].extend(f"{source['id']}: {w}" for w in source["warnings"])
    write_json(output / "collection.json", collection)
    write(output / "events.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    return manifest, collection, events


def annotations(manifest_path, manifest, events):
    if not manifest.get("annotations"):
        return {"summary": "No reviewed qualitative annotations supplied.", "decisions": []}
    value = json.loads((manifest_path.parent / manifest["annotations"]).read_text(encoding="utf-8"))
    by_id = {event["id"]: event for event in events}
    if not isinstance(value.get("summary"), str) or not isinstance(value.get("decisions"), list):
        raise ValueError("Annotations need a summary string and a decisions array.")
    seen = set()
    for decision in value["decisions"]:
        for field in ("input", "title", "category", "analysis"):
            if not isinstance(decision.get(field), str):
                raise ValueError(f"Annotation requires string field {field}.")
        human = by_id.get(decision["input"])
        if not human or human["kind"] != "user" or human["id"] in seen:
            raise ValueError(f"Invalid or duplicate annotation input: {decision['input']}")
        seen.add(human["id"])
        if decision.get("response"):
            response = by_id.get(decision["response"])
            if (not response or response["kind"] != "assistant"
                    or (human["harness"], human["session"]) != (response["harness"], response["session"])
                    or events.index(response) < events.index(human)):
                raise ValueError(f"Invalid subsequent response evidence: {decision['response']}")
        for field, text in decision.items():
            if isinstance(text, str):
                decision[field] = redact(text)
    value["summary"] = redact(value["summary"])
    return value


def analyze(collection, events, interpretation):
    accounting = account(events, collection.get("pricing", {}).get("card"))
    coverage = accounting["total"]
    cost = (
        f'Estimated API-equivalent {"priced subtotal" if coverage["unpriced_calls"] else "total"}: ${coverage["estimated_usd"]:,.6f} USD. '
        f'{coverage["priced_calls"]} of {coverage["calls"]} observed requests priced. '
        f'Frozen rate card dated {accounting["pricing"]["verified_on"]}; not an actual bill.'
        if coverage["estimated_usd"] is not None else
        "Spend unavailable: no observed requests have a supplied model price."
    )
    usages = [e for e in events if e["kind"] == "usage"]
    models, harnesses = defaultdict(list), defaultdict(list)
    for event in usages:
        models[event["model"]].append(event)
    for event in events:
        harnesses[event["harness"]].append(event)

    def totals(rows):
        metered = [e for e in rows if e["kind"] == "usage"]
        return {
            "human_inputs": sum(e["kind"] == "user" for e in rows),
            "assistant_messages": sum(e["kind"] == "assistant" for e in rows),
            "tool_calls": sum(e["kind"] == "tool" for e in rows),
            "metered_requests": len(metered),
            "input_tokens": sum(e["input"] for e in metered) if metered else None,
            "output_tokens": sum(e["output"] for e in metered) if metered else None,
            "cache_read_tokens": sum(e["cached_read"] for e in metered) if metered else None,
            "cache_write_tokens": sum(e["cache_write"] for e in metered) if metered else None,
        }

    total_tokens = sum(e["input"] + e["output"] for e in usages)
    model_mix = [
        {"model": model, "requests": len(rows),
         "input": sum(e["input"] for e in rows), "output": sum(e["output"] for e in rows),
         "token_percent": 100 * sum(e["input"] + e["output"] for e in rows) / total_tokens if total_tokens else 0}
        for model, rows in sorted(models.items())
    ]
    sensitivity = []
    for minutes in (5, 10, 15):
        bins = {}
        for kind in ("user", "usage"):
            bins[kind] = {e["t"] // (minutes * 60000) for e in events
                          if e["kind"] == kind and e["t"] is not None}
        sensitivity.append({
            "minutes": minutes, "human_bins": len(bins["user"]),
            "model_bins": len(bins["usage"]), "overlap_bins": len(bins["user"] & bins["usage"]),
            "model_without_input_bins": len(bins["usage"] - bins["user"]),
        })
    hourly = Counter()
    zone = ZoneInfo(collection["timezone"])
    for event in events:
        if event["t"] is not None and event["kind"] in ("user", "usage"):
            hour = datetime.fromtimestamp(event["t"] / 1000, zone).strftime("%Y-%m-%d %H:00 %Z")
            hourly[(hour, event["kind"])] += 1
    dated = [e["t"] for e in events if e["t"] is not None]
    return {
        **totals(events), "title": collection["title"], "timezone": collection["timezone"],
        "conversations": len({(e["harness"], e["session"]) for e in events}),
        "undated_events": sum(e["t"] is None for e in events),
        "start": min(dated) if dated else None, "end": max(dated) if dated else None,
        "harnesses": {key: totals(value) for key, value in harnesses.items()},
        "models": model_mix, "activity_bins": sensitivity,
        "hourly": [{"hour": hour, "human_inputs": hourly[(hour, "user")],
                    "model_events": hourly[(hour, "usage")]} for hour in sorted({h for h, _ in hourly})],
        "interpretation": interpretation,
        "cost": cost, "accounting": accounting,
        "limitations": [
            "Observed log coverage is not a guarantee of complete project history.",
            "Input totals include cached input; reasoning output is not counted a second time.",
            "Metered usage is not a subscription bill or evidence of comparative model quality.",
            "Per-input request ownership uses recorded turn IDs when available, otherwise inferred same-conversation proximity. Missing/unmatched inputs remain unattributed; turn costs cover complete recorded turns, not just elapsed playback.",
            "Occupied bins are not working hours, runtime, latency, or proof of unattended work.",
            "Historical assistant completion claims are agent-reported, not independently verified here.",
            "Pasted or AI-drafted user material does not establish original authorship.",
            "Unknown timestamps remain outside the timeline; tool payloads and internal reasoning are not displayed.",
            "Credential redaction is best effort; private outputs require review before sharing.",
        ],
    }


def discover(harness, root):
    patterns = {"copilot": ("**/events.jsonl",), "claude": ("**/*.jsonl",),
                "codex": ("**/rollout-*.jsonl",), "cursor": ("**/*.md", "**/*.json"),
                "replit": ("**/*.md", "**/*.json")}
    if not root.is_dir():
        raise ValueError("Discovery root must be an explicitly approved directory.")
    found = sorted({p for pattern in patterns[harness] for p in root.glob(pattern) if p.is_file()})
    print(json.dumps([{"harness": harness, "path": str(p.resolve()), "bytes": p.stat().st_size}
                      for p in found], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("discover", help="Inventory an explicitly approved source directory.")
    inventory.add_argument("--harness", choices=HARNESSES, required=True)
    inventory.add_argument("--root", type=Path, required=True)
    for command, help_text in (
        ("prepare", "Freeze and normalize sources for qualitative annotation; not a published review."),
        ("run", "Collect, analyze, render, and verify a local review."),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("--manifest", type=Path, required=True)
        command_parser.add_argument("--output", type=Path, required=True)
        if command == "run":
            command_parser.add_argument("--public-base-url", help="HTTPS site directory for PDF hyperlinks; HTML stays offline-compatible.")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "discover":
            discover(args.harness, args.root)
            return
        manifest_path = args.manifest.resolve()
        manifest, collection, events = collect(manifest_path, args.output.resolve())
        interpretation = ({"summary": "Qualitative review pending.", "decisions": []}
                          if args.command == "prepare" else annotations(manifest_path, manifest, events))
        analysis = analyze(collection, events, interpretation)
        write_json(args.output / "analysis.json", analysis)
        if args.command == "prepare":
            print(f"Prepared evidence for annotation (not a completed review): {args.output.resolve()}")
            return
        from render import publish
        publish(args.output.resolve(), collection, events, analysis, args.public_base_url)
        print(f"Verified review: {args.output.resolve() / 'report.html'}")
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Review failed: {error}\n")


if __name__ == "__main__":
    main()
