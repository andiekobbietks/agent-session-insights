"""Cache-aware API-equivalent estimates and explicit per-input attribution."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import math


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


def price_request(event, card):
    rate = (card or {}).get("models", {}).get(event["model"])
    if rate is None:
        return {"estimated_usd": None, "long_context": False,
                "price_note": "No supplied price for the recorded model."}
    fresh = event["input"] - event.get("cached_read", 0) - event.get("cache_write", 0)
    if fresh < 0:
        raise ValueError("Cached tokens exceed inclusive input.")
    long = event["input"] > rate.get("long_context_threshold", math.inf)
    im = Decimal(str(rate.get("long_input_multiplier", 1) if long else 1))
    om = Decimal(str(rate.get("long_output_multiplier", 1) if long else 1))
    cost = (
        (Decimal(fresh) * Decimal(str(rate["input"]))
         + Decimal(event.get("cached_read", 0)) * Decimal(str(rate["cache_read"]))
         + Decimal(event.get("cache_write", 0)) * Decimal(str(rate["cache_write"]))) * im
        + Decimal(event["output"]) * Decimal(str(rate["output"])) * om
    ) / Decimal(1000000)
    return {"estimated_usd": float(cost), "long_context": long,
            "price_note": "API-equivalent estimate, not actual billed spend."}


def summarize(rows):
    priced = [r for r in rows if r.get("estimated_usd") is not None]
    return {
        "calls": len(rows), "input": sum(r["input"] for r in rows),
        "output": sum(r["output"] for r in rows),
        "cached_read": sum(r.get("cached_read", 0) for r in rows),
        "cache_write": sum(r.get("cache_write", 0) for r in rows),
        "estimated_usd": float(sum((Decimal(str(r["estimated_usd"])) for r in priced), Decimal(0))) if priced else None,
        "priced_calls": len(priced), "unpriced_calls": len(rows) - len(priced),
        "long_context_calls": sum(r.get("long_context", False) for r in rows),
        "models": sorted({r["model"] for r in rows}),
    }


def account(events, card=None):
    if card is not None:
        validate_rates(card)
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

    requests = []
    for event in events:
        if event["kind"] != "usage":
            continue
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
        requests.append({**event, **price_request(event, card),
                         "turn": owner["id"] if owner else None, "attribution": method})
    turns = []
    for human in humans:
        rows = [r for r in requests if r["turn"] == human["id"]]
        turns.append({
            "id": human["id"], "harness": human["harness"], "session": human["session"],
            "text": human["text"], "t": human.get("t"), **summarize(rows),
            "attribution": dict(Counter(r["attribution"] for r in rows)),
        })
    unassigned = [r for r in requests if r["turn"] is None]
    pricing = {
        "currency": "USD", "verified_on": card["verified_on"] if card else None,
        "basis": card.get("basis", "Standard API-equivalent token processing.") if card else "No pricing supplied.",
        "sources": card.get("sources", {}) if card else {},
        "rates": card.get("models", {}) if card else {},
        "verified_on_by_model_note": card.get("verified_on_by_model_note", "") if card else "",
        "assumptions": card.get("assumptions", []) if card else [],
        "method": "Inclusive input is split into fresh, cached-read and cache-write tokens. Apply the supplied model rates and long-context multipliers per request; output already includes reasoning. Estimates exclude subscriptions, tools, compute, taxes and discounts.",
    }
    return {"pricing": pricing, "requests": requests, "turns": turns,
            "total": summarize(requests), "unattributed": summarize(unassigned)}
