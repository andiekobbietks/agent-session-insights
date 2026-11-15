"""Cache-aware API-equivalent estimates and explicit per-input attribution."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re


def validate_rates(card):
    if card.get("currency") != "USD" or card.get("unit") != "per million tokens":
        raise ValueError("Pricing must specify USD per million tokens.")
    date.fromisoformat(card["verified_on"])
    if not isinstance(card.get("models"), dict) or not isinstance(card.get("sources"), dict):
        raise ValueError("Pricing needs models and source references.")
    for model, rate in card["models"].items():
        if rate is None:
            continue
        if rate.get("source") not in card["sources"]:
            raise ValueError(f"{model}: pricing source reference is missing.")
        for field in ("input", "cache_read", "cache_write", "output",
                      "long_input_multiplier", "long_output_multiplier"):
            value = rate.get(field, 1) if field.endswith("multiplier") else rate.get(field)
            try:
                valid = not isinstance(value, bool) and math.isfinite(float(value)) and Decimal(str(value)) >= 0
            except (TypeError, ValueError, InvalidOperation):
                valid = False
            if not valid:
                raise ValueError(f"{model}: invalid {field} rate.")
        threshold = rate.get("long_context_threshold")
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
            raise ValueError(f"{model}: invalid long-context threshold.")


def model_rate(model, card):
    models = (card or {}).get("models", {})
    if model in models:
        return models[model]
    if model.startswith("claude-"):
        canonical = re.sub(r"-\d{8}$", "", model).replace(".", "-")
        return models.get(canonical)
    return None


def price_request(event, card, previous=None):
    rate = model_rate(event["model"], card)
    if rate is None:
        return {"estimated_usd": None, "estimated_usd_min": None, "estimated_usd_max": None, "long_context": False,
                "price_note": "No supplied price for the recorded model."}
    if not event.get("input_complete", True):
        return {"estimated_usd": None, "estimated_usd_min": None, "estimated_usd_max": None, "long_context": False,
                "price_note": "Inclusive input is unknown: this source records fresh input without complete cache counts."}
    read_known = event.get("cached_read_known", "cached_read" in event)
    write_known = event.get("cache_write_known", "cache_write" in event)
    read = event.get("cached_read", 0) if read_known else 0
    written = event.get("cache_write", 0) if write_known else 0
    remaining = event["input"] - read - written
    if remaining < 0:
        raise ValueError("Cached tokens exceed inclusive input.")
    long = event["input"] > rate.get("long_context_threshold", math.inf)
    im = Decimal(str(rate.get("long_input_multiplier", 1) if long else 1))
    om = Decimal(str(rate.get("long_output_multiplier", 1) if long else 1))
    fresh_rate, read_rate, write_rate = (Decimal(str(rate[key])) for key in ("input", "cache_read", "cache_write"))
    fixed = Decimal(read) * read_rate + Decimal(written) * write_rate
    choices = [fresh_rate] + ([] if read_known else [read_rate]) + ([] if write_known else [write_rate])
    output_cost = Decimal(event["output"]) * Decimal(str(rate["output"])) * om

    def cost(input_cost):
        return float((input_cost * im + output_cost) / Decimal(1000000))

    lower = cost(fixed + Decimal(remaining) * min(choices))
    upper = cost(fixed + Decimal(remaining) * max(choices))
    inferred = None
    if (not read_known and previous and previous.get("input_complete", True)
            and (previous["harness"], previous["session"]) == (event["harness"], event["session"])
            and previous["model"] == event["model"]
            and previous["source"].split(":")[0] == event["source"].split(":")[0]
            and previous.get("t") is not None and event.get("t") is not None
            and 0 < event["t"] - previous["t"] <= 300000
            and event["input"] >= previous["input"]):
        inferred = min(previous["input"], remaining)
    missing = not read_known or not write_known
    estimate = lower if lower == upper else None
    if not missing:
        estimate = cost(fixed + Decimal(remaining) * fresh_rate)
    elif inferred is not None:
        estimate = cost(fixed + Decimal(inferred) * read_rate + Decimal(remaining - inferred) * fresh_rate)
    note = "Retail API-equivalent estimate, not actual billed spend."
    if missing:
        note += " Missing-cache range covers possible fresh/read/write allocations; not a confidence interval."
    if inferred is not None:
        note += f" Assumed append-only context reuse from {previous['id']}: {inferred} cached-read tokens; unrecorded writes assumed zero for the point estimate. Cache hits are not verified."
    components = None
    if estimate is not None and (not missing or inferred is not None):
        priced_read = read + (inferred or 0)
        components = {
            key: {"tokens": count, "estimated_usd": float(Decimal(count) * price * multiplier / Decimal(1000000))}
            for key, count, price, multiplier in (
                ("fresh_input", event["input"] - priced_read - written, fresh_rate, im),
                ("cache_read", priced_read, read_rate, im), ("cache_write", written, write_rate, im),
                ("output", event["output"], Decimal(str(rate["output"])), om),
            )
        }
    return {"estimated_usd": estimate, "estimated_usd_min": lower, "estimated_usd_max": upper,
            "estimated_cached_read": inferred, "cost_components": components,
            "long_context": long, "price_note": note}


def summarize(rows):
    priced = [r for r in rows if r.get("estimated_usd_min", r.get("estimated_usd")) is not None]
    complete_points = priced and all(r.get("estimated_usd") is not None for r in priced)
    components = None
    if priced and all(r.get("cost_components") is not None for r in priced):
        components = {
            key: {
                "tokens": sum(r["cost_components"][key]["tokens"] for r in priced),
                "estimated_usd": float(sum((Decimal(str(r["cost_components"][key]["estimated_usd"]))
                                            for r in priced), Decimal(0))),
            } for key in ("fresh_input", "cache_read", "cache_write", "output")
        }
    return {
        "calls": len(rows), "input": sum(r["input"] for r in rows),
        "input_complete": all(r.get("input_complete", True) for r in rows),
        "output": sum(r["output"] for r in rows),
        "cached_read": sum(r.get("cached_read", 0) for r in rows),
        "cache_write": sum(r.get("cache_write", 0) for r in rows),
        "estimated_usd": float(sum((Decimal(str(r["estimated_usd"])) for r in priced), Decimal(0))) if complete_points else None,
        "estimated_usd_min": float(sum((Decimal(str(r.get("estimated_usd_min", r["estimated_usd"]))) for r in priced), Decimal(0))) if priced else None,
        "estimated_usd_max": float(sum((Decimal(str(r.get("estimated_usd_max", r["estimated_usd"]))) for r in priced), Decimal(0))) if priced else None,
        "cache_estimated_calls": sum(not r.get("cached_read_known", True) or not r.get("cache_write_known", True) for r in rows),
        "cost_components": components,
        "priced_calls": len(priced), "unpriced_calls": len(rows) - len(priced),
        "long_context_calls": sum(r.get("long_context", False) for r in rows),
        "models": sorted({r["model"] for r in rows}),
    }


def input_attributions(events):
    humans = [e for e in events if e["kind"] == "user"]
    groups, recorded, responses = defaultdict(list), defaultdict(list), {}
    positions = {e["id"]: i for i, e in enumerate(events)}
    for human in humans:
        conversation = human["harness"], human["session"]
        groups[conversation].append(human)
        if human.get("turn_id"):
            recorded[(*conversation, human["turn_id"])].append(human)
    for event in events:
        if event["kind"] == "assistant":
            identity = event.get("request_id") or event.get("identity")
            if identity is not None:
                responses.setdefault((event["harness"], event["session"], identity), event)

    def preceding(candidates, anchor):
        if anchor.get("t") is not None:
            eligible = [h for h in candidates if h.get("t") is not None and
                        (h["t"], positions[h["id"]]) <= (anchor["t"], positions[anchor["id"]])]
        else:
            file, line = anchor["source"].split(":")
            eligible = [h for h in candidates if h["source"].split(":")[0] == file
                        and int(h["source"].split(":")[1]) <= int(line)]
            return max(eligible, key=lambda h: int(h["source"].split(":")[1]), default=None)
        return max(eligible, key=lambda h: positions[h["id"]], default=None)

    def attribute(event):
        conversation = event["harness"], event["session"]
        owner, method = None, "Unattributed: no preceding input in scope."
        if event.get("turn_id"):
            candidates = recorded[(*conversation, event["turn_id"])]
            if len(candidates) == 1:
                owner, method = candidates[0], "Recorded turn ID."
            elif candidates:
                owner = preceding(candidates, event)
                method = "Shared recorded turn ID; preceding input (inferred)."
            else:
                method = "Unattributed: recorded turn ID has no input in scope."
        else:
            response = responses.get((*conversation, event.get("identity")))
            owner = preceding(groups[conversation], response or event)
            if owner:
                method = "Matching response's preceding input (inferred)." if response else (
                    "Preceding input in the same source (inferred)." if event.get("t") is None
                    else "Preceding input by timestamp in the same conversation (inferred).")
        return owner, method

    requests = {(event["harness"], event["session"], event["identity"]): event
                for event in events if event["kind"] == "usage" and event.get("identity") is not None}
    attributed = {event["id"]: attribute(event) for event in events if event["kind"] == "usage"}
    for event in events:
        if event["kind"] != "assistant":
            continue
        identity = event.get("request_id") or event.get("identity")
        request = requests.get((event["harness"], event["session"], identity))
        attributed[event["id"]] = (attributed[request["id"]] if request and not event.get("turn_id")
                                    else attribute(event))
    return attributed


def account(events, card=None):
    if card is not None:
        validate_rates(card)
    humans = [e for e in events if e["kind"] == "user"]
    attributed = input_attributions(events)
    requests, previous_requests, replies = [], {}, defaultdict(list)
    for event in events:
        if event["kind"] == "assistant":
            owner, method = attributed[event["id"]]
            if owner:
                replies[owner["id"]].append((event, method))
        if event["kind"] != "usage":
            continue
        conversation = event["harness"], event["session"]
        owner, method = attributed[event["id"]]
        requests.append({**event, **price_request(event, card, previous_requests.get(conversation)),
                         "turn": owner["id"] if owner else None, "attribution": method})
        previous_requests[conversation] = event
    turns = []
    for human in humans:
        rows = [r for r in requests if r["turn"] == human["id"]]
        responses = replies[human["id"]]
        times = [response["t"] for response, _ in responses]
        duration = (max(times) - human["t"] if human.get("t") is not None and times
                    and all(t is not None and t >= human["t"] for t in times) else None)
        turns.append({
            "id": human["id"], "harness": human["harness"], "session": human["session"],
            "text": human["text"], "t": human.get("t"), **summarize(rows),
            "attribution": dict(Counter(r["attribution"] for r in rows)),
            "duration_ms": duration,
            "response_ids": [response["id"] for response, _ in responses],
            "response_attribution": dict(Counter(method for _, method in responses)),
        })
    unassigned = [r for r in requests if r["turn"] is None]
    pricing = {
        "currency": "USD", "verified_on": card["verified_on"] if card else None,
        "basis": card.get("basis", "Standard API-equivalent token processing.") if card else "No pricing supplied.",
        "sources": card.get("sources", {}) if card else {},
        "rates": {r["model"]: model_rate(r["model"], card) for r in requests},
        "verified_on_by_model_note": card.get("verified_on_by_model_note", "") if card else "",
        "assumptions": card.get("assumptions", []) if card else [],
        "method": "Inclusive input is split into fresh, cached-read and cache-write tokens. Recorded counts, including zero, take precedence. Missing cache reads use a labeled append-only context-reuse heuristic for consecutive same-model requests in the same source/session within five minutes; otherwise show a range, not a guessed percentage. Bounds allow all missing fresh/read/write allocations and are not confidence intervals. Apply the frozen retail rates and long-context multipliers per request; output already includes reasoning. Estimates exclude subscriptions, tools, compute, taxes and discounts.",
    }
    return {"pricing": pricing, "requests": requests, "turns": turns,
            "total": summarize(requests), "unattributed": summarize(unassigned)}
