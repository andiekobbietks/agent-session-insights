import json
from pathlib import Path
import sys
import tempfile
import unittest

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import payload, player_html
from review import analyze

PRICING = {
    "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
    "sources": {"test": "https://example.com/pricing"},
    "models": {"m": {"input": 2, "cache_read": 0.1, "cache_write": 2.5,
                     "output": 10, "source": "test"}},
}


class PlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 1000})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def open_player(self, rows, usages=(), pricing=None):
        events = []
        for i, (kind, time, body, session) in enumerate(rows):
            events.append({"id": f'{"H" if kind == "user" else "R"}{i+1:03d}',
                           "kind": kind, "harness": "cursor", "session": session,
                           "source": f"S01:{i+1}", "text": body, "timestamp": None, "t": time})
        for i, usage in enumerate(usages, 1):
            events.append({"id": f"A{i:03d}", "identity": f"call-{i}", "kind": "usage",
                           "harness": "cursor", "session": "one", "source": f"S01:{1000+i}",
                           "timestamp": None, "cached_read": 0, "cache_write": 0, **usage})
        events.sort(key=lambda e: (e["t"] is None, e["t"] or 0, int(e["source"].split(":")[1])))
        collection = {"title": "Player test", "timezone": "America/Los_Angeles"}
        if pricing is not None:
            collection["pricing"] = {"card": pricing}
        analysis = analyze(collection, events, {"summary": "Test", "decisions": []})
        data = payload(events, analysis)
        path = Path(self.directory.name) / "player.html"
        path.write_text(player_html(data))
        self.page.goto(path.as_uri(), wait_until="networkidle")
        return data

    def state(self):
        return self.page.evaluate("window.vennPlayback.state()")

    def test_adjacent_message_controls_zoom_time_ticks_and_full_record_search(self):
        start = 1794071182211
        data = self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 30000, "First response", "one"),
            ("user", start + 120000, "Second request", "one"),
            ("assistant", start + 3600000, "Final response", "one"),
        ])
        self.assertEqual(self.state()["message"], "H001")
        self.assertEqual(self.page.locator("#prev-text").inner_text(), "←")
        self.assertEqual(self.page.locator("#next-text").inner_text(), "→")
        controls = self.page.locator(".text-controls").bounding_box()
        arrows = self.page.locator(".text-arrows").bounding_box()
        self.assertAlmostEqual(arrows["x"] + arrows["width"], controls["x"] + controls["width"], delta=1)
        self.page.locator("#next-text").click()
        self.assertEqual(self.page.locator("#agent-text").inner_text(), "First response")
        self.page.locator("#prev-text").click()
        self.assertEqual(self.state()["message"], "H001")
        width = self.state()["hi"] - self.state()["lo"]
        self.page.locator("#zoom-in").click()
        self.assertLess(self.state()["hi"] - self.state()["lo"], width)
        self.assertEqual(len(self.page.locator(".axis-label").all_text_contents()), 7)
        self.assertTrue(all(":" in value for value in self.page.locator(".axis-label").all_text_contents()))
        self.page.locator("#search").fill("Final response")
        self.page.locator("#event-list .event-row").click()
        self.assertEqual(self.state()["message"], data["updates"][-1]["id"])
        self.assertEqual(self.page.locator("#agent-text").inner_text(), "Final response")
        self.page.locator("#zoom-reset").click()
        self.assertEqual(self.state()["hi"] - self.state()["lo"], width)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        controls = self.page.locator(".text-controls").bounding_box()
        arrows = self.page.locator(".text-arrows").bounding_box()
        self.assertAlmostEqual(arrows["x"] + arrows["width"], controls["x"] + controls["width"], delta=1)
        self.assertFalse(self.errors)

    def test_drag_selection_zooms_in_both_directions_and_scrollbar_pans(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First", "one"),
            ("assistant", start + 3600000, "Last", "one"),
        ])
        width = self.state()["hi"] - self.state()["lo"]
        for left, right in ((.2, .6), (.7, .3)):
            if not self.page.locator("#zoom-reset").is_disabled():
                self.page.locator("#zoom-reset").click()
            self.page.locator("#timeline").scroll_into_view_if_needed()
            box = self.page.locator("#timeline").bounding_box()
            x = lambda fraction: box["x"] + (150 + fraction * 930) / 1100 * box["width"]
            y = box["y"] + box["height"] * .55
            self.page.mouse.move(x(left), y)
            self.page.mouse.down()
            self.page.mouse.move(x(right), y, steps=8)
            self.assertTrue(self.page.locator("#zoom-selection").is_visible())
            self.assertAlmostEqual(self.state()["hi"] - self.state()["lo"], width, delta=1)
            self.page.mouse.up()
            self.assertAlmostEqual(self.state()["lo"], start + min(left, right) * width, delta=width * .005)
            self.assertAlmostEqual(self.state()["hi"] - self.state()["lo"], abs(right - left) * width, delta=width * .005)
            self.assertAlmostEqual(self.state()["now"], self.state()["lo"], delta=1)
            self.assertEqual(self.page.locator("#zoom-selection").count(), 0)
        zoom_width = self.state()["hi"] - self.state()["lo"]
        bar = self.page.locator("#timeline-scroll")
        self.assertGreater(bar.evaluate("(el)=>el.scrollWidth-el.clientWidth"), 0)
        self.assertGreater(bar.evaluate("(el)=>el.offsetHeight-el.clientHeight"), 0)
        bar.evaluate("(el)=>{el.scrollLeft=el.scrollWidth-el.clientWidth;}")
        self.page.wait_for_function("Math.abs(window.vennPlayback.state().hi-window.vennPlayback.state().scopeHi)<1")
        self.assertAlmostEqual(self.state()["hi"] - self.state()["lo"], zoom_width, delta=1)
        bar.evaluate("(el)=>{el.scrollLeft=0;}")
        self.page.wait_for_function("Math.abs(window.vennPlayback.state().lo-window.vennPlayback.state().scopeLo)<1")
        self.page.locator("#zoom-reset").click()
        self.assertAlmostEqual(self.state()["hi"] - self.state()["lo"], width, delta=1)
        self.assertEqual(bar.get_attribute("aria-disabled"), "true")
        self.assertFalse(self.errors)

    def test_marker_drag_does_not_trigger_marker_click_and_cancel_preserves_zoom(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First", "one"),
            ("user", start + 1200000, "Middle", "one"),
            ("assistant", start + 3600000, "Last", "one"),
        ])
        self.page.locator("#timeline circle").nth(1).click()
        self.assertEqual(self.state()["message"], "H002")
        self.page.locator("#start").click()
        self.page.locator("#timeline").scroll_into_view_if_needed()
        dot = self.page.locator("#timeline circle").first.bounding_box()
        x, y = dot["x"] + dot["width"] / 2, dot["y"] + dot["height"] / 2
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 150, y, steps=6)
        self.page.mouse.up()
        self.assertLess(self.state()["hi"] - self.state()["lo"], 3600000)
        self.assertAlmostEqual(self.state()["now"], self.state()["lo"], delta=1)
        before = (self.state()["lo"], self.state()["hi"])
        box = self.page.locator("#timeline").bounding_box()
        x, y = box["x"] + box["width"] * .4, box["y"] + box["height"] * .55
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 100, y, steps=4)
        self.page.locator("#timeline").dispatch_event("pointercancel", {"pointerId": 1})
        self.page.mouse.up()
        self.assertEqual((self.state()["lo"], self.state()["hi"]), before)
        self.assertEqual(self.page.locator("#zoom-selection").count(), 0)
        self.assertFalse(self.errors)

    def test_undated_navigation_and_hostile_transcript_are_plain_text(self):
        hostile = "</script><script>window.pwned=1</script>"
        self.open_player([
            ("user", None, hostile, "one"),
            ("assistant", None, "Undated reply", "one"),
            ("user", None, "More", "one"),
        ])
        self.assertEqual(self.page.locator("#clock").inner_text(), "Timing unavailable")
        self.assertIsNone(self.page.evaluate("window.pwned"))
        self.assertEqual(self.page.locator("#human-text").inner_text(), hostile)
        self.page.locator("#next-text").click()
        self.assertEqual(self.page.locator("#agent-text").inner_text(), "Undated reply")
        self.page.locator("#next-text").click()
        self.assertEqual(self.page.locator("#human-text").inner_text(), "More")
        self.page.locator("#prev-text").click()
        self.assertEqual(self.state()["message"], "R002")
        self.assertTrue(self.page.locator("#zoom-in").is_disabled())
        self.assertFalse(self.errors)

    def test_same_timestamp_messages_and_other_conversation_do_not_leak_responses(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "One", "one"),
            ("assistant", start, "Reply to one", "one"),
            ("user", start + 1000, "Two", "two"),
            ("assistant", start + 2000, "Reply to two", "two"),
        ])
        self.page.evaluate("window.vennPlayback.jumpToMessage('H001')")
        self.assertNotIn("Reply to one", self.page.locator("#agent-text").inner_text())
        self.page.locator("#next-text").click()
        self.assertEqual(self.state()["message"], "R002")
        self.assertEqual(self.page.locator("#agent-text").inner_text(), "Reply to one")
        self.page.locator("#next-text").click()
        self.assertEqual(self.state()["message"], "H003")
        self.assertNotIn("Reply to one", self.page.locator("#agent-text").inner_text())
        self.assertNotIn("Reply to two", self.page.locator("#agent-text").inner_text())
        self.assertFalse(self.errors)

    def test_usage_totals_turns_drag_scroll_and_day_harness_filters(self):
        start = 1794071182211
        data = self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 1800000, "First response", "one"),
            ("user", start + 70000000, "Second request", "one"),
            ("assistant", start + 86400000, "Final response", "one"),
        ], [
            {"t": start + 600000, "model": "m", "input": 100000, "output": 10000, "cached_read": 20000},
            {"t": start + 1200000, "harness": "copilot", "model": "m", "input": 50000, "output": 5000},
            {"t": start + 80000000, "model": "m", "input": 200000, "output": 20000, "cached_read": 40000},
        ], PRICING)
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.9360")
        self.assertEqual(self.page.locator("#session-spend").count(), 1)
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.assertFalse(self.page.locator("#usage-details").evaluate("(el)=>el.open"))
        self.assertLess(self.page.locator(".usage-card").bounding_box()["height"], 120)
        self.assertEqual(self.page.locator("#model-usage tr").count(), 1)
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.9360")
        self.assertEqual(self.page.locator("#session-tokens").inner_text(), "350,000 in · 35,000 out · 3 calls")
        self.assertIn("Models: m", self.page.locator("#session-models").inner_text())
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.2620")
        self.assertEqual(self.page.locator("#turn-usage-title").inner_text(), "Turn estimate")
        self.assertIn("Complete recorded turn", self.page.locator("#prompt-usage").get_attribute("title"))
        self.assertIn("inferred", self.page.locator("#turn-attribution").text_content())
        self.assertEqual(self.page.locator("#turn-models").inner_text(), "m")
        prompt = self.page.locator("#human-text").bounding_box()
        footer = self.page.locator("#prompt-usage").bounding_box()
        self.assertGreaterEqual(footer["y"], prompt["y"] + prompt["height"])
        self.assertTrue(self.page.evaluate("document.getElementById('human-text').nextElementSibling.id==='prompt-usage'"))
        first_log = self.page.locator('#event-list [data-id="H001"] .event-usage')
        self.assertIn("m · Est. $0.2620 · 100,000 in · 10,000 out", first_log.inner_text())
        self.assertIn("$0.5240", self.page.locator('#event-list [data-id="H003"] .event-usage').inner_text())
        self.assertEqual(self.page.locator('#event-list [data-id="R002"] .event-usage').count(), 0)
        self.assertNotIn("First response", self.page.locator("#agent-text").inner_text())
        self.assertEqual(self.page.locator("#turn-usage-rows tr").count(), 3)
        self.assertIn("Unattributed", self.page.locator("#turn-usage-rows").text_content())
        self.page.locator("#timeline").scroll_into_view_if_needed()
        box = self.page.locator("#timeline").bounding_box()
        x = lambda fraction: box["x"] + (150 + fraction * 930) / 1100 * box["width"]
        y = box["y"] + box["height"] * .55
        self.page.mouse.move(x(.002), y)
        self.page.mouse.down()
        self.page.mouse.move(x(.25), y, steps=8)
        self.page.mouse.up()
        self.assertTrue(self.page.locator("#window-summary").is_visible())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.4120")
        self.page.locator("#timeline-scroll").evaluate("(el)=>{el.scrollLeft=el.scrollWidth-el.clientWidth;}")
        self.page.wait_for_function("document.getElementById('window-spend').textContent==='$0.5240'")
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.9360")
        self.page.locator("#harness").select_option("GitHub Copilot")
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.1500")
        self.page.locator("#harness").select_option("Cursor")
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.7860")
        self.page.locator("#harness").select_option("all")
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.page.locator("#day").select_option(data["days"][1]["date"])
        self.assertTrue(self.page.locator("#window-summary").is_visible())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.5240")
        self.page.locator("#day").select_option("all")
        self.page.evaluate("window.vennPlayback.jumpToMessage('H003')")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.5240")
        self.assertIn("200,000 in · 20,000 out", self.page.locator("#turn-tokens").inner_text())
        self.page.locator('#event-list [data-id="H001"]').click()
        self.assertEqual(self.state()["selected"], "H001")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.2620")
        self.page.locator("#usage-details").evaluate("(el)=>el.open=true")
        self.page.locator("#model-usage").evaluate("(el)=>el.closest('details').open=true")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.assertFalse(self.errors)

    def test_undated_partial_and_missing_prices_stay_unknown_not_zero(self):
        rows = [("user", None, "Untimed request", "one"), ("assistant", None, "Untimed answer", "one")]
        usages = [
            {"t": None, "model": "m", "input": 100000, "output": 10000, "cached_read": 20000},
            {"t": None, "model": "</script><script>window.pwned=1</script>", "input": 10, "output": 1},
        ]
        self.open_player(rows, usages, PRICING)
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.2620 + unpriced calls")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.2620 + unpriced calls")
        self.assertIn("1 / 2 recorded calls priced", self.page.locator("#session-models").inner_text())
        self.assertIn("100,010 in · 10,001 out", self.page.locator("#session-tokens").inner_text())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "No recorded usage")
        self.assertIn("timing unavailable", self.page.locator("#window-coverage").inner_text())
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.assertIn("$0.2620 + unpriced calls", self.page.locator('#event-list [data-id="H001"] .event-usage').inner_text())
        self.assertEqual(self.page.locator('#event-list [data-id="R002"] .event-usage').count(), 0)
        self.assertIsNone(self.page.evaluate("window.pwned"))
        self.open_player(rows, usages)
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "Unpriced")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "Unpriced")
        self.page.locator("#usage-details > summary").click()
        self.assertIn("Spend is unknown, not zero", self.page.locator("#pricing-note").inner_text())
        self.assertIn("Unpriced", self.page.locator('#event-list [data-id="H001"] .event-usage').inner_text())
        self.assertFalse(self.errors)

    def test_prompt_footer_is_hidden_without_a_user_and_does_not_carry_over(self):
        start = 1794071182211
        self.open_player([
            ("assistant", start, "Earlier agent context", "one"),
            ("user", start + 1000, "Request", "one"),
            ("assistant", start + 2000, "Response", "one"),
        ], [{"t": start + 1500, "model": "m", "input": 100, "output": 20}], PRICING)
        self.assertTrue(self.page.locator("#prompt-usage").is_hidden())
        self.page.evaluate("window.vennPlayback.jumpToMessage('H002')")
        self.assertTrue(self.page.locator("#prompt-usage").is_visible())
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.0004")
        self.page.evaluate("window.vennPlayback.jumpToMessage('R001')")
        self.assertTrue(self.page.locator("#prompt-usage").is_hidden())
        self.assertEqual(self.page.locator("#turn-spend").text_content(), "")
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
