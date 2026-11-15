import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounting import account, price_request, summarize, validate_rates

CARD = {
    "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
    "sources": {"test": "https://example.com/pricing"},
    "models": {"m": {"input": 2, "cache_read": 0.1, "cache_write": 2.5, "output": 10,
                     "long_context_threshold": 272000, "long_input_multiplier": 2,
                     "long_output_multiplier": 1.5, "source": "test"}},
}


def event(id, kind, t=0, session="one", **fields):
    row = {"id": id, "kind": kind, "t": t, "harness": "codex", "session": session,
           "identity": id, "source": f"S01:{int(id[1:])}", "text": id}
    if kind == "usage":
        row.update(model="m", input=100, output=20, cached_read=40, cache_write=10)
    return {**row, **fields}


class AccountingTests(unittest.TestCase):
    def test_turn_durations_follow_recorded_ownership_and_final_replies_across_overlaps(self):
        events = [
            event("H001", "user", 0, turn_id="first"),
            event("H002", "user", 1000, turn_id="second"),
            event("R003", "assistant", 2000, turn_id="first"),
            event("R004", "assistant", 3000, turn_id="second"),
            event("R005", "assistant", 4000, request_id="first-call"),
            event("H006", "user", 500, session="two"),
            event("R007", "assistant", 2000, session="two"),
            event("A008", "usage", 5000, identity="first-call", turn_id="first"),
        ]
        result = account(sorted(events, key=lambda row: row["t"]))
        turns = {turn["id"]: turn for turn in result["turns"]}
        self.assertEqual(turns["H001"]["duration_ms"], 4000)
        self.assertEqual(turns["H002"]["duration_ms"], 2000)
        self.assertEqual(turns["H006"]["duration_ms"], 1500)
        self.assertEqual(turns["H001"]["response_ids"], ["R003", "R005"])
        self.assertEqual(turns["H002"]["response_ids"], ["R004"])
        self.assertEqual(result["requests"][0]["turn"], "H001")

    def test_turn_durations_preserve_zero_and_reject_missing_or_reversed_timing(self):
        events = [
            event("H001", "user", 0, turn_id="complete"),
            event("R002", "assistant", 0, turn_id="complete"),
            event("H003", "user", None, turn_id="undated-prompt"),
            event("R004", "assistant", 100, turn_id="undated-prompt"),
            event("H005", "user", 200, turn_id="undated-reply"),
            event("R006", "assistant", 300, turn_id="undated-reply"),
            event("R007", "assistant", None, turn_id="undated-reply"),
            event("H008", "user", 400, turn_id="reversed"),
            event("R009", "assistant", 350, turn_id="reversed"),
            event("H010", "user", 500, turn_id="unanswered"),
        ]
        result = account(events)
        self.assertEqual([turn["duration_ms"] for turn in result["turns"]], [0, None, None, None, None])

    def test_inclusive_cache_and_strict_long_context_boundary(self):
        usage = event("A001", "usage", input=272000, output=100, cached_read=200000, cache_write=1000)
        short = price_request(usage, CARD)
        self.assertAlmostEqual(short["estimated_usd"], (71000 * 2 + 200000 * .1 + 1000 * 2.5 + 100 * 10) / 1e6)
        self.assertFalse(short["long_context"])
        long = price_request({**usage, "input": 272001}, CARD)
        self.assertTrue(long["long_context"])
        self.assertAlmostEqual(long["estimated_usd"], ((71001 * 2 + 200000 * .1 + 1000 * 2.5) * 2 + 100 * 10 * 1.5) / 1e6)
        total = summarize([{**usage, **short}, {**usage, **long}])
        self.assertAlmostEqual(sum(c["estimated_usd"] for c in total["cost_components"].values()), total["estimated_usd"])
        self.assertEqual(total["cost_components"]["cache_read"]["tokens"], 400000)
        self.assertAlmostEqual(long["cost_components"]["cache_write"]["estimated_usd"], .005)
        missing = price_request({**usage, "cached_read_known": False}, CARD)
        self.assertIsNone(missing["cost_components"])

    def test_partial_unknown_and_free_pricing_are_distinct(self):
        known, unknown = event("A001", "usage"), event("A002", "usage", model="not-listed")
        rows = [{**r, **price_request(r, CARD)} for r in (known, unknown)]
        total = summarize(rows)
        self.assertEqual((total["priced_calls"], total["unpriced_calls"]), (1, 1))
        self.assertEqual(total["estimated_usd"], rows[0]["estimated_usd"])
        self.assertIsNone(price_request(known, None)["estimated_usd"])
        self.assertIsNone(summarize(rows[1:])["estimated_usd"])
        self.assertIsNone(summarize([])["estimated_usd"])
        free = copy.deepcopy(CARD)
        free["models"]["m"].update(input=0, output=0, cache_read=0, cache_write=0)
        self.assertEqual(price_request(known, free)["estimated_usd"], 0)
        bad = copy.deepcopy(CARD)
        bad["models"]["m"]["cache_read"] = float("nan")
        with self.assertRaises(ValueError):
            validate_rates(bad)

    def test_missing_cache_is_a_range_until_consecutive_context_supports_an_estimate(self):
        first = event("A001", "usage", 0, input=100, cached_read=0, cache_write=0,
                      cached_read_known=False, cache_write_known=True)
        second = {**first, "id": "A002", "t": 1000, "input": 150, "source": "S01:2"}
        result = account([first, second], CARD)
        one, two = result["requests"]
        self.assertIsNone(one["estimated_usd"])
        self.assertAlmostEqual(one["estimated_usd_min"], (100 * .1 + 20 * 10) / 1e6)
        self.assertAlmostEqual(one["estimated_usd_max"], (100 * 2 + 20 * 10) / 1e6)
        self.assertEqual(two["estimated_cached_read"], 100)
        self.assertAlmostEqual(two["estimated_usd"], (50 * 2 + 100 * .1 + 20 * 10) / 1e6)
        self.assertIn("not verified", two["price_note"])
        self.assertEqual(result["total"]["priced_calls"], 2)
        self.assertIsNone(result["total"]["estimated_usd"])
        for field in ("estimated_usd_min", "estimated_usd_max"):
            self.assertAlmostEqual(result["total"][field], one[field] + two[field])
        self.assertFalse(first["cached_read_known"])
        self.assertEqual(first["cached_read"], 0)

    def test_explicit_zero_and_context_boundaries_prevent_false_cache_inference(self):
        previous = event("A001", "usage", 0)
        missing = event("A002", "usage", 1000, input=200, cached_read=0, cache_write=0,
                        cached_read_known=False, cache_write_known=False)
        for change in ({"input": 50}, {"t": 300001}, {"t": 0}, {"t": None},
                       {"session": "two"}, {"source": "S02:2"}, {"model": "other"}):
            with self.subTest(change=change):
                result = price_request({**missing, **change}, CARD, previous)
                self.assertIsNone(result["estimated_usd"])
                self.assertIsNone(result.get("estimated_cached_read"))
        explicit = {**missing, "cached_read_known": True, "cache_write_known": True}
        result = price_request(explicit, CARD, previous)
        self.assertIsNone(result["estimated_cached_read"])
        self.assertAlmostEqual(result["estimated_usd"], (200 * 2 + 20 * 10) / 1e6)
        self.assertEqual(result["estimated_usd_min"], result["estimated_usd_max"])
        incomplete = price_request({**missing, "input_complete": False}, CARD, previous)
        self.assertIsNone(incomplete["estimated_usd_min"])

    def test_unknown_cache_writes_expand_bounds_and_dated_claude_ids_resolve(self):
        usage = event("A001", "usage", input=100, cached_read=0, cache_write=0,
                      cached_read_known=False, cache_write_known=False)
        result = price_request(usage, CARD)
        self.assertAlmostEqual(result["estimated_usd_max"], (100 * 2.5 + 20 * 10) / 1e6)
        card = copy.deepcopy(CARD)
        card["models"]["claude-sonnet-4-5"] = card["models"].pop("m")
        dated = price_request({**usage, "model": "claude-sonnet-4-5-20250929"}, card)
        dotted = price_request({**usage, "model": "claude-sonnet-4.5"}, card)
        self.assertEqual(dated["estimated_usd_max"], result["estimated_usd_max"])
        self.assertEqual(dotted["estimated_usd_max"], result["estimated_usd_max"])

    def test_recorded_ids_override_timing_isolate_sessions_and_reconcile(self):
        events = [
            event("A001", "usage", -1),
            event("H002", "user", 0, turn_id="first"),
            event("H003", "user", 1, session="two", turn_id="first"),
            event("H004", "user", 2, turn_id="second"),
            event("A005", "usage", 3, turn_id="first"),
            event("A006", "usage", 4, session="two", turn_id="first"),
            event("A007", "usage", 5, turn_id="second"),
            event("A008", "usage", 6, turn_id="missing"),
            event("A009", "usage", 7),
            event("H010", "user", 8),
        ]
        result = account(events, CARD)
        self.assertEqual([r["turn"] for r in result["requests"]], [None, "H002", "H003", "H004", None, "H004"])
        self.assertEqual(result["unattributed"]["calls"], 2)
        for field in ("calls", "input", "output", "cached_read", "cache_write"):
            self.assertEqual(sum(t[field] for t in result["turns"]) + result["unattributed"][field], result["total"][field])
        self.assertAlmostEqual(sum(t["estimated_usd"] or 0 for t in result["turns"]) +
                               result["unattributed"]["estimated_usd"], result["total"]["estimated_usd"])
        self.assertIsNone(result["turns"][-1]["estimated_usd"])
        self.assertIn("inferred", result["requests"][-1]["attribution"])

    def test_same_time_and_shared_recorded_ids_use_source_order(self):
        events = [event("H001", "user", turn_id="shared"), event("A002", "usage", turn_id="shared"),
                  event("H003", "user", turn_id="shared"), event("A004", "usage", turn_id="shared")]
        result = account(events, CARD)
        self.assertEqual([r["turn"] for r in result["requests"]], ["H001", "H003"])
        self.assertTrue(all("inferred" in r["attribution"] for r in result["requests"]))

    def test_response_link_prevents_delayed_usage_from_moving_to_queued_prompt(self):
        events = [event("H001", "user", 0), event("R002", "assistant", 1, request_id="request"),
                  event("H003", "user", 2), event("A004", "usage", 3, identity="request")]
        result = account(events, CARD)
        self.assertEqual(result["requests"][0]["turn"], "H001")
        self.assertIn("response", result["requests"][0]["attribution"])

    def test_undated_attribution_uses_source_lines_not_mixed_time_sort(self):
        events = [event("H002", "user", 1, source="S01:20"),
                  event("H001", "user", None, source="S01:10"),
                  event("A003", "usage", None, source="S01:30"),
                  event("A004", "usage", None, source="S02:40")]
        result = account(events, CARD)
        self.assertEqual([r["turn"] for r in result["requests"]], ["H002", None])
        self.assertEqual(result["total"]["calls"], 2)


if __name__ == "__main__":
    unittest.main()
