import json
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import payload, player_html, report_html
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

    def open_player(self, rows, usages=(), pricing=None, decisions=(), message_fields=None):
        events = []
        for i, (kind, time, body, session) in enumerate(rows):
            events.append({"id": f'{"H" if kind == "user" else "R"}{i+1:03d}',
                           "kind": kind, "harness": "cursor", "session": session,
                           "source": f"S01:{i+1}", "text": body, "timestamp": None, "t": time,
                           **(message_fields or {}).get(f'{"H" if kind == "user" else "R"}{i+1:03d}', {})})
        for i, usage in enumerate(usages, 1):
            events.append({"id": f"A{i:03d}", "identity": f"call-{i}", "kind": "usage",
                           "harness": "cursor", "session": "one", "source": f"S01:{1000+i}",
                           "timestamp": None, "cached_read": 0, "cache_write": 0, **usage})
        events.sort(key=lambda e: (e["t"] is None, e["t"] or 0, int(e["source"].split(":")[1])))
        collection = {"title": "Player test", "timezone": "America/Los_Angeles"}
        if pricing is not None:
            collection["pricing"] = {"card": pricing}
        analysis = analyze(collection, events, {"summary": "Test", "decisions": list(decisions)})
        self.events, self.analysis = events, analysis
        data = payload(events, analysis)
        path = Path(self.directory.name) / "player.html"
        path.write_text(player_html(data), encoding="utf-8")
        self.page.goto(path.as_uri(), wait_until="networkidle")
        return data

    def state(self):
        return self.page.evaluate("window.vennPlayback.state()")

    def test_all_prompt_replies_stay_listed_and_selecting_one_preserves_layout(self):
        start = 1794071182211
        last_reply = "Final reply\n" * 300
        self.open_player([
            ("user", start, "First prompt", "one"),
            ("assistant", start + 1000, "First reply", "one"),
            ("user", start + 1500, "Other session prompt", "two"),
            ("assistant", start + 1700, "Other session reply", "two"),
            ("assistant", start + 2000, last_reply, "one"),
            ("user", start + 3000, "Next prompt", "one"),
            ("assistant", start + 4000, "Next turn reply", "one"),
        ])
        player = Path(self.directory.name) / "player.html"
        wrapper = player.with_name("embedded.html")
        wrapper.write_text('<iframe src="player.html" style="width:100%;height:1000px"></iframe>', encoding="utf-8")
        geometry = """() => ({
            height:document.documentElement.scrollHeight,
            logTop:document.getElementById('event-list').getBoundingClientRect().top+scrollY,
            widths:['.activity','#agent-text','#event-list'].map(selector=>document.querySelector(selector).clientWidth)
        })"""
        for width in (1280, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            for embedded in (False, True):
                with self.subTest(width=width, embedded=embedded):
                    self.page.goto((wrapper if embedded else player).as_uri(), wait_until="networkidle")
                    frame = self.page.frames[1] if embedded else self.page.main_frame
                    responses = frame.locator("#agent-text .response-text")
                    self.assertEqual(responses.all_text_contents(), ["First reply", last_reply])
                    self.assertEqual(frame.evaluate("window.vennPlayback.state().message"), "H001")
                    self.assertEqual(frame.locator("#update-title").inner_text(), "Agent responses (2)")
                    self.assertIn("R002", frame.locator(".response-meta").first.inner_text())
                    self.assertIn("S01:5", frame.locator(".response-meta").last.inner_text())
                    self.assertTrue(frame.locator(".turn-content").evaluate("el=>el.scrollHeight>el.clientHeight"))
                    self.assertFalse(frame.locator("#agent-text").evaluate("el=>el.scrollHeight>el.clientHeight"))
                    before = frame.evaluate(geometry)
                    frame.locator(".agent-response").first.evaluate("el=>el.dataset.cached='kept'")
                    frame.evaluate("window.vennPlayback.jumpToMessage('R005')")
                    self.assertEqual(frame.locator(".agent-response.active").get_attribute("data-id"), "R005")
                    self.assertEqual(frame.locator(".agent-response").first.get_attribute("data-cached"), "kept")
                    self.assertEqual(responses.all_text_contents(), ["First reply", last_reply])
                    self.assertGreater(frame.locator(".turn-content").evaluate("el=>el.scrollTop"), 0)
                    self.assertEqual(frame.locator("#agent-text").evaluate("el=>el.scrollTop"), 0)
                    self.assertEqual(frame.evaluate(geometry), before)
                    frame.evaluate("window.vennPlayback.jumpToMessage('H006')")
                    self.assertEqual(responses.all_text_contents(), ["Next turn reply"])
                    self.assertEqual(frame.evaluate(geometry), before)
                    frame.evaluate("window.vennPlayback.jumpToMessage('H001')")
                    self.assertEqual(responses.all_text_contents(), ["First reply", last_reply])
                    self.assertEqual(frame.locator(".turn-content").evaluate("el=>el.scrollTop"), 0)
                    self.assertEqual(frame.evaluate(geometry), before)
        self.assertFalse(self.errors)

    def test_all_undated_replies_remain_plain_text_and_follow_scoped_navigation(self):
        hostile = "</script><script>window.pwned=1</script>\nVerbatim reply"
        self.open_player([
            ("user", None, "First input", "one"),
            ("assistant", None, hostile, "one"),
            ("assistant", None, "Second reply", "one"),
            ("user", None, "Next input", "one"),
            ("assistant", None, "Next turn reply", "one"),
            ("user", None, "Other session", "two"),
            ("assistant", None, "Other reply", "two"),
        ])
        replies = self.page.locator("#agent-text .response-text")
        self.assertEqual(replies.all_text_contents(), [hostile, "Second reply"])
        self.assertIsNone(self.page.evaluate("window.pwned"))
        self.assertIn("Timestamp unavailable", self.page.locator(".response-meta").first.inner_text())
        self.page.evaluate("window.vennPlayback.jumpToMessage('R003')")
        self.assertEqual(replies.all_text_contents(), [hostile, "Second reply"])
        self.assertEqual(self.page.locator(".agent-response.active").get_attribute("data-id"), "R003")
        self.page.locator("#session").select_option('["Cursor","two"]')
        self.assertEqual(replies.all_text_contents(), ["Other reply"])
        self.page.locator("#session").select_option('["Cursor","one"]')
        self.assertEqual(replies.all_text_contents(), [hostile, "Second reply"])
        self.assertEqual(self.page.locator("#message-number").inner_text(), "1 of 2")
        self.page.locator("#next-text").click()
        self.assertEqual(replies.all_text_contents(), ["Next turn reply"])
        self.assertEqual(self.page.locator("#human-text").inner_text(), "Next input")
        self.assertFalse(self.errors)

    def test_response_groups_follow_recorded_ownership_instead_of_the_latest_prompt(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First input", "one"),
            ("user", start + 1000, "Queued input", "one"),
            ("assistant", start + 2000, "First input progress", "one"),
            ("assistant", start + 3000, "Queued input reply", "one"),
            ("assistant", start + 4000, "First input final reply", "one"),
            ("assistant", start + 5000, "Unattributed reply", "one"),
        ], usages=[{"t": start + 5000, "model": "m", "input": 1000, "output": 100,
                    "identity": "late-call", "turn_id": "first"}],
           message_fields={"H001": {"turn_id": "first"}, "H002": {"turn_id": "second"},
                           "R003": {"turn_id": "first"}, "R004": {"turn_id": "second"},
                           "R005": {"request_id": "late-call"}, "R006": {"turn_id": "missing"}})
        replies = self.page.locator("#agent-text .response-text")
        self.assertEqual(replies.all_text_contents(), ["First input progress", "First input final reply"])
        self.page.evaluate("window.vennPlayback.jumpToMessage('R005')")
        self.assertEqual(self.page.locator("#human-text").inner_text(), "First input")
        self.assertEqual(self.state()["selected"], "H001")
        self.assertEqual(replies.all_text_contents(), ["First input progress", "First input final reply"])
        self.page.evaluate("window.vennPlayback.jumpToMessage('H002')")
        self.assertEqual(replies.all_text_contents(), ["Queued input reply"])
        self.page.evaluate("window.vennPlayback.jumpToMessage('R006')")
        self.assertEqual(replies.all_text_contents(), ["Unattributed reply"])
        self.assertIsNone(self.state()["selected"])
        self.assertIn("No user input", self.page.locator("#human-text").inner_text())
        self.assertFalse(self.errors)

    def test_session_filter_preserves_totals_and_shows_own_reply(self):
        start = 1794071182211
        data = self.open_player([
            ("user", start, "First session prompt", "one"),
            ("assistant", start + 1000, "First session reply", "one"),
            ("user", start + 86400000, "Second session prompt", "two"),
            ("assistant", start + 86401000, "Second session reply", "two"),
        ], [
            {"t": start + 1000, "model": "m", "input": 100000, "output": 1000},
            {"t": start + 86401000, "session": "two", "model": "m", "input": 200000, "output": 2000},
        ], PRICING)
        total = self.page.locator("#session-spend").inner_text()
        self.page.locator("#day").select_option(data["days"][0]["date"])
        self.page.locator("#session").select_option('["Cursor","two"]')
        self.assertEqual(self.page.locator("#day").input_value(), "all")
        self.assertEqual(self.page.locator("#session-spend").inner_text(), total)
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.42")
        self.assertEqual(self.page.locator("#human-text").inner_text(), "Second session prompt")
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Second session reply")
        self.assertEqual(self.page.locator("#event-list button").count(), 2)
        labels = self.page.locator("#session option").all_text_contents()
        self.assertIn("2026", labels[1])
        self.assertIn("First session prompt", labels[1])
        self.assertIn("Second session prompt", labels[2])
        self.page.locator("#usage-details").evaluate("(el) => el.open = true")
        self.page.locator("#model-scope").click()
        self.assertIn("200,000 / $0.40", self.page.locator("#model-usage").inner_text())
        self.assertFalse(self.errors)

    def test_active_log_row_scrolls_with_manual_navigation(self):
        start = 1794071182211
        self.open_player([
            (role, start + i * 1000, f"Message {i}", "one")
            for i, role in enumerate(["user", "assistant"] * 80)
        ])
        self.page.evaluate("window.vennPlayback.jumpToMessage('H159')")
        self.assertGreater(self.page.locator("#event-list").evaluate("(el) => el.scrollTop"), 0)
        self.assertTrue(self.page.locator("#event-list").evaluate("""el => {
            const list = el.getBoundingClientRect(), row = el.querySelector('.active').getBoundingClientRect();
            return row.top >= list.top && row.bottom <= list.bottom;
        }"""))
        self.page.evaluate("window.vennPlayback.jumpToMessage('H001')")
        self.assertEqual(self.page.locator("#event-list").evaluate("(el) => el.scrollTop"), 0)
        self.assertFalse(self.errors)

    def test_log_separates_id_role_harness_and_message_columns(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First prompt", "one"),
            ("assistant", start + 1000, "Reply", "one"),
            ("user", start + 2000, "Other harness prompt", "two"),
            ("user", None, "Undated prompt", "three"),
        ], usages=[{"t": start + 500, "model": "m", "input": 100, "output": 10}], pricing=PRICING,
           message_fields={"H001": {"harness": "copilot"}, "R002": {"harness": "copilot"},
                           "H003": {"harness": "claude"}, "H004": {"harness": "codex"}})
        url = self.page.url
        selectors = [".event-id", ".stamp", ".event-role", ".event-harness", ".preview"]
        for width in (1280, 390):
            for log_only in (False, True):
                with self.subTest(width=width, log_only=log_only):
                    self.page.set_viewport_size({"width": width, "height": 900})
                    self.page.goto(url + ("?view=log" if log_only else ""), wait_until="networkidle")
                    for identifier, role, harness in (
                        ("H001", "User", "GitHub Copilot"), ("R002", "Agent", "GitHub Copilot"),
                        ("H003", "User", "Claude Code"), ("H004", "User", "Codex"),
                    ):
                        row = self.page.locator(f'.event-row[data-id="{identifier}"]')
                        self.assertEqual(row.locator(".event-id").inner_text(), identifier)
                        self.assertEqual(row.locator(".event-role").inner_text(), role)
                        self.assertEqual(row.locator(".event-harness").inner_text(), harness)
                        boxes = [row.locator(selector).bounding_box() for selector in selectors]
                        columns = boxes if width == 1280 else boxes[:4]
                        for left, right in zip(columns, columns[1:]):
                            self.assertLessEqual(left["x"] + left["width"], right["x"])
                        if width == 390:
                            self.assertGreaterEqual(boxes[4]["y"], max(box["y"] + box["height"] for box in columns))
                        self.assertTrue(row.evaluate("el => el.scrollWidth <= el.clientWidth"))
                    self.assertEqual(self.page.locator('[data-id="H004"] .stamp').inner_text(), "Undated")
                    prompt = self.page.locator('.event-row[data-id="H001"]')
                    self.assertEqual(prompt.locator(".event-usage").count(), 1)
                    self.assertEqual(self.page.locator('[data-id="R002"] .event-usage').count(), 0)
                    self.assertEqual(prompt.locator(".preview").bounding_box()["x"],
                                     prompt.locator(".event-usage").bounding_box()["x"])
                    self.page.locator('.event-row[data-id="R002"]').click()
                    self.assertEqual(self.state()["message"], "R002")
                    self.assertFalse(self.errors)

    def test_session_log_scrolls_independently_with_wheel_and_keyboard(self):
        start = 1794071182211
        for width in (1280, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.open_player([
                    (role, start + i * 1000, f"Message {i}", "one")
                    for i, role in enumerate(["user", "assistant"] * 12)
                ])
                self.page.evaluate("""() => {
                    const spacer = document.createElement('div');
                    spacer.style.height = '100vh';
                    document.body.append(spacer);
                }""")
                log = self.page.get_by_role("region", name="Full session log", exact=True)
                self.assertTrue(log.evaluate("el => el.scrollHeight > el.clientHeight"))
                self.assertLessEqual(log.evaluate("el => el.clientHeight"), 360)
                log.scroll_into_view_if_needed()
                log.hover()
                before = self.page.evaluate("({scroll:scrollY,search:document.getElementById('search').getBoundingClientRect().top})")
                self.page.mouse.wheel(0, 150)
                self.page.wait_for_function("document.getElementById('event-list').scrollTop > 0")
                self.assertEqual(self.page.evaluate("scrollY"), before["scroll"])
                self.assertEqual(self.page.locator("#search").bounding_box()["y"], before["search"])
                log.focus()
                log.evaluate("el => el.scrollTop = 0")
                state = self.state()
                self.page.keyboard.press("ArrowDown")
                self.page.wait_for_function("document.getElementById('event-list').scrollTop > 0")
                self.assertEqual(self.state(), state)
                log.evaluate("el => el.scrollTop = el.scrollHeight")
                log.hover()
                outer_scroll = self.page.evaluate("scrollY")
                self.page.mouse.wheel(0, 200)
                self.page.wait_for_timeout(200)
                self.assertEqual(self.page.evaluate("scrollY"), outer_scroll)
                self.assertFalse(self.errors)

    def test_selecting_log_row_preserves_list_scroll_even_when_zoom_repositions(self):
        start = 1794071182211
        self.open_player([
            (role, start + i * 60000, f"Message {i}", "one")
            for i, role in enumerate(["user", "assistant"] * 40)
        ])
        self.page.locator("#zoom-in").click()
        self.page.locator("#event-list").scroll_into_view_if_needed()
        selection = self.page.locator("#event-list").evaluate("""el => {
            const row = el.querySelector('[data-id="R070"]');
            el.scrollTop = row.offsetTop + row.offsetHeight - el.clientHeight - 10;
            const before = el.scrollTop;
            row.click();
            return {before, after: el.scrollTop};
        }""")
        self.assertEqual(selection["before"], selection["after"])
        self.assertEqual(self.state()["message"], "R070")
        self.assertEqual(self.state()["now"], start + 69 * 60000)
        self.assertEqual(self.page.locator("#event-list .active").get_attribute("data-id"), "R070")
        coordinates = self.page.locator("#event-list").evaluate("""el => {
            const row = el.querySelector('[data-id="R068"]');
            el.scrollTop = row.offsetTop + row.offsetHeight - el.clientHeight - 10;
            const bounds = el.getBoundingClientRect(), item = row.getBoundingClientRect();
            return {x:item.x+item.width/2, y:Math.min(bounds.bottom,item.bottom)-5, top:el.scrollTop};
        }""")
        self.page.mouse.click(coordinates["x"], coordinates["y"])
        self.assertEqual(self.state()["message"], "R068")
        self.assertEqual(self.page.locator("#event-list").evaluate("(el) => el.scrollTop"), coordinates["top"])
        self.assertFalse(self.errors)

    def test_player_page_height_stays_constant_for_short_and_long_messages(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "Short input", "one"),
            ("assistant", start + 1000, "Short response", "one"),
            ("user", start + 2000, "Long input\n" * 500, "one"),
            ("assistant", start + 3000, "Long response\n" * 500, "one"),
        ], decisions=[{"input": "H001", "title": "Direction", "category": "Scope", "analysis": "Fixture"}])
        player = Path(self.directory.name) / "player.html"
        wrapper = player.with_name("embedded.html")
        wrapper.write_text('<iframe src="player.html" style="width:100%;height:1000px"></iframe>', encoding="utf-8")
        for width in (1280, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            for embedded in (False, True):
                self.page.goto((wrapper if embedded else player).as_uri(), wait_until="networkidle")
                frame = self.page.frames[1] if embedded else self.page.main_frame
                before = frame.evaluate("""() => ({
                    height:document.documentElement.scrollHeight,
                    logTop:document.getElementById('event-list').getBoundingClientRect().top+scrollY,
                    widths:['.activity','#human-text','#agent-text','#event-list'].map(selector =>
                        document.querySelector(selector).clientWidth),
                    arrowsRight:document.querySelector('.text-arrows').getBoundingClientRect().right
                })""")
                for message in ("R002", "H003", "R004", "H001"):
                    frame.evaluate("(id) => window.vennPlayback.jumpToMessage(id)", message)
                    after = frame.evaluate("""() => ({
                        height:document.documentElement.scrollHeight,
                        logTop:document.getElementById('event-list').getBoundingClientRect().top+scrollY,
                        widths:['.activity','#human-text','#agent-text','#event-list'].map(selector =>
                            document.querySelector(selector).clientWidth),
                        arrowsRight:document.querySelector('.text-arrows').getBoundingClientRect().right
                    })""")
                    self.assertEqual(after, before, (width, embedded, message))
        self.assertFalse(self.errors)

    def test_compact_layout_and_readable_mobile_timeline(self):
        start = 1794071182211
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.open_player([("user", start, "Prompt", "one"), ("assistant", start + 60000, "Reply", "one")])
        self.assertEqual(self.page.locator(".shortcut").count(), 0)
        self.assertTrue(self.page.evaluate("""() => {
            const filters = document.querySelector('.filters').getBoundingClientRect();
            const controls = [...document.querySelectorAll('.filters > label,.filters > button:not([hidden])')];
            const bottom = Math.max(...controls.map(el => el.getBoundingClientRect().bottom));
            const timeline = document.querySelector('.timeline').getBoundingClientRect();
            return filters.bottom - bottom <= 1 && timeline.top - bottom <= 10;
        }"""))
        self.assertLess(self.page.locator(".activity").bounding_box()["y"], 700)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        self.assertGreaterEqual(self.page.locator("#timeline").bounding_box()["width"], 600)
        self.page.wait_for_function("""() => {
            const el = document.querySelector('.axis-label');
            return parseFloat(getComputedStyle(el).fontSize) * el.getScreenCTM().a >= 11;
        }""")
        self.assertTrue(self.page.locator(".axis-label").first.evaluate("""el => {
            const scale = el.getScreenCTM().a;
            return parseFloat(getComputedStyle(el).fontSize) * scale >= 11;
        }"""))
        self.assertLess(self.page.locator(".activity").bounding_box()["y"], 844)
        self.assertFalse(self.errors)

    def test_day_and_night_bands_follow_local_time_dst_and_zoom(self):
        zone = ZoneInfo("America/Los_Angeles")
        for month, day, morning_hours in ((3, 8, 5), (11, 1, 7)):
            with self.subTest(month=month):
                start = round(datetime(2026, month, day, tzinfo=zone).timestamp() * 1000)
                finish = round(datetime(2026, month, day + 1, tzinfo=zone).timestamp() * 1000)
                data = self.open_player([
                    ("user", start, "Midnight input", "one"),
                    ("assistant", finish, "Next midnight reply", "one"),
                ])
                bands = data["day_night"]
                self.assertEqual([band["kind"] for band in bands], ["night", "day", "night"])
                self.assertEqual([(band["end"] - band["start"]) / 3600000 for band in bands],
                                 [morning_hours, 12, 6])
                self.assertIn("Day 06:00–18:00", self.page.locator(".legend").inner_text())
                self.assertIn("America/Los_Angeles", self.page.locator(".legend").inner_text())
                for action in (None, "#zoom-in", "#pan-next", "#zoom-reset"):
                    if action:
                        self.page.locator(action).click()
                    state = self.state()
                    visible = self.page.locator(".time-band").evaluate_all("""bands => bands.map(band => ({
                        start:Number(band.dataset.start), end:Number(band.dataset.end),
                        width:Number(band.getAttribute('width')), pointerEvents:getComputedStyle(band).pointerEvents
                    }))""")
                    self.assertEqual(visible[0]["start"], state["lo"])
                    self.assertEqual(visible[-1]["end"], state["hi"])
                    for i, band in enumerate(visible):
                        self.assertGreater(band["width"], 0)
                        self.assertEqual(band["pointerEvents"], "none")
                        if i:
                            self.assertEqual(band["start"], visible[i - 1]["end"])
                self.assertFalse(self.errors)

    def test_reviewed_directions_do_not_overlay_day_and_night_shading(self):
        zone = ZoneInfo("America/Los_Angeles")
        start = round(datetime(2026, 11, 24, 17, 30, tzinfo=zone).timestamp() * 1000)
        finish = round(datetime(2026, 11, 24, 18, 32, tzinfo=zone).timestamp() * 1000)
        data = self.open_player([
            ("user", start, "First direction", "one"),
            ("user", finish, "Second direction", "one"),
        ], decisions=[
            {"input": "H001", "title": "First direction", "category": "Scope", "analysis": "First"},
            {"input": "H002", "title": "Second direction", "category": "Scope", "analysis": "Second"},
        ])
        self.assertNotIn("clusters", data)
        self.assertEqual(self.page.locator("#clusters, .cluster-card, .legend .cluster").count(), 0)
        self.assertEqual(self.page.locator("#timeline > rect:not(.time-band)").count(), 1)
        self.assertEqual(self.page.locator(".timeline-day, .timeline-night").count(), 2)
        self.assertEqual(self.page.locator("#timeline polygon").count(), 2)
        self.assertEqual(self.page.locator("#moments .moment-item").count(), 2)
        self.page.locator("#moments .moment-item").last.click()
        self.assertEqual(self.state()["message"], "H002")
        self.assertFalse(self.errors)

    def test_embedded_player_hides_duplicate_header_and_footer(self):
        start = 1794071182211
        self.open_player([("user", start, "Prompt", "one")])
        wrapper = Path(self.directory.name) / "embedded.html"
        wrapper.write_text('<iframe src="player.html" style="width:1200px;height:900px"></iframe>', encoding="utf-8")
        self.page.goto(wrapper.as_uri(), wait_until="networkidle")
        frame = self.page.frame_locator("iframe")
        self.assertFalse(frame.locator("header").is_visible())
        self.assertFalse(frame.locator("footer").is_visible())
        self.assertFalse(frame.locator(".browse").is_visible())
        self.assertTrue(frame.locator(".activity").is_visible())
        self.assertFalse(self.errors)

    def test_report_and_embedded_views_share_theme_and_follow_live_changes(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "Prompt", "one"),
            ("assistant", start + 1000, "Reply", "one"),
        ])
        collection = {"title": "Player test", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        report = Path(self.directory.name) / "report.html"
        report.write_text(report_html(collection, self.events, self.analysis), encoding="utf-8")
        for theme, system in (("light", "dark"), ("dark", "light")):
            with self.subTest(theme=theme, system=system):
                self.page.emulate_media(color_scheme=system, reduced_motion="reduce")
                self.page.goto(report.as_uri() + f"?scoutTheme={theme}", wait_until="networkidle")
                views = self.page.frames
                self.assertEqual(len(views), 3)
                for frame in views:
                    frame.wait_for_function("document.documentElement.dataset.theme === " + json.dumps(theme))
                appearance = """() => ({
                    background:getComputedStyle(document.body).backgroundColor,
                    color:getComputedStyle(document.body).color,
                    font:getComputedStyle(document.body).fontFamily
                })"""
                expected = self.page.evaluate(appearance)
                for frame in views[1:]:
                    self.assertEqual(frame.evaluate(appearance), expected)
                    self.assertEqual(
                        frame.locator("#next-idea").evaluate("el => getComputedStyle(el).backgroundColor"),
                        frame.locator(".deck").evaluate("el => getComputedStyle(el).backgroundColor"),
                    )
                if theme == "light":
                    self.assertEqual(expected["background"], "rgb(255, 255, 255)")
                    red, green, blue = map(int, expected["color"].removeprefix("rgb(").removesuffix(")").split(","))
                    self.assertGreater(blue, red)
                    self.assertGreater(blue, green)
                changed = "dark" if theme == "light" else "light"
                self.page.evaluate("theme => document.documentElement.dataset.theme = theme", changed)
                for frame in views:
                    frame.wait_for_function("document.documentElement.dataset.theme === " + json.dumps(changed))
                expected = self.page.evaluate(appearance)
                for frame in views[1:]:
                    self.assertEqual(frame.evaluate(appearance), expected)
        self.assertFalse(self.errors)

    def test_standalone_theme_follows_system_and_honors_explicit_override(self):
        self.open_player([("user", 1794071182211, "Prompt", "one")])
        url = self.page.url
        for view in ("", "?view=log"):
            self.page.emulate_media(color_scheme="light")
            self.page.goto(url + view, wait_until="networkidle")
            self.page.emulate_media(color_scheme="dark")
            self.page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
            self.page.emulate_media(color_scheme="light")
            self.page.wait_for_function("document.documentElement.dataset.theme === 'light'")
            separator = "&" if view else "?"
            for theme in ("light", "dark"):
                with self.subTest(view=view, theme=theme):
                    self.page.emulate_media(color_scheme="dark" if theme == "light" else "light")
                    self.page.goto(url + view + separator + f"scoutTheme={theme}", wait_until="networkidle")
                    self.assertEqual(self.page.locator("html").get_attribute("data-theme"), theme)
        self.assertFalse(self.errors)

    def test_report_session_log_is_a_separate_pane_and_navigates_replay(self):
        start = 1794071182211
        self.open_player([
            (role, start + i * 1000, f"Message {i}", "one")
            for i, role in enumerate(["user", "assistant"] * 12)
        ] + [("user", None, "Undated prompt", "one"),
             ("assistant", None, "Undated reply", "one")])
        collection = {"title": "Player test", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        report = Path(self.directory.name) / "report.html"
        report.write_text(report_html(collection, self.events, self.analysis), encoding="utf-8")
        for width in (1280, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(report.as_uri(), wait_until="networkidle")
                replay = next(frame for frame in self.page.frames if frame.url.endswith("/player.html"))
                log = next(frame for frame in self.page.frames if frame.url.endswith("?view=log"))
                self.assertEqual(self.page.locator("#session-log").evaluate("el => el.parentElement.tagName"), "MAIN")
                self.assertEqual(self.page.locator("#session-log").evaluate("el => el.previousElementSibling.id"), "playback")
                self.assertFalse(replay.locator(".browse").is_visible())
                self.assertFalse(log.locator(".workspace").is_visible())
                self.assertFalse(log.locator(".deck").is_visible())
                self.assertFalse(log.locator("#search").is_visible())
                search = self.page.get_by_role("searchbox", name="Search full session log")
                self.assertTrue(search.is_visible())
                if width == 1280:
                    heading = self.page.locator("#session-log h2").bounding_box()
                    self.assertLess(abs(search.bounding_box()["y"] - heading["y"]), 15)
                self.assertEqual(log.locator(".event-row").count(), 26)
                self.assertTrue(log.locator("#event-list").evaluate("el => el.scrollHeight > el.clientHeight"))
                self.assertTrue(log.evaluate("document.documentElement.scrollHeight <= innerHeight"))
                self.assertTrue(log.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                search.fill("R022")
                log.wait_for_function("document.querySelectorAll('.event-row').length === 1", polling=100)
                self.assertEqual(log.locator(".event-row").count(), 1)
                log.locator(".event-row").click()
                replay.wait_for_function("window.vennPlayback.state().message === 'R022'", polling=100)
                replay.locator("#next-text").click()
                log.wait_for_function("window.vennPlayback.state().message === 'H023'", polling=100)
                self.assertEqual(search.input_value(), "R022")
                search.fill("")
                log.wait_for_function("document.querySelectorAll('.event-row').length === 26", polling=100)
                self.assertEqual(log.locator(".event-row.active").get_attribute("data-id"), "H023")
                search.fill("Undated reply")
                log.wait_for_function("document.querySelectorAll('.event-row').length === 1", polling=100)
                log.locator(".event-row").click()
                replay.wait_for_function("window.vennPlayback.state().message === 'R026'", polling=100)
                self.page.emulate_media(media="print")
                self.assertFalse(self.page.locator(".session-log-frame").is_visible())
                self.page.emulate_media(media="screen")
                self.assertFalse(self.errors)

    def test_replay_has_one_scrollbar_per_pane_and_no_iframe_scrollbar(self):
        start = 1794071182211
        rows = [
            (role, start + (i // 4) * 7200000 + (i % 4) * 1000,
             ("Long message\n" * 500) if i < 2 else f"Message {i}", "one")
            for i, role in enumerate(["user", "assistant"] * 40)
        ]
        decisions = [{"input": f"H{i+1:03d}", "title": f"Direction {i}", "category": "Scope",
                      "analysis": "Recorded direction"} for i in range(0, 80, 2)]
        self.open_player(rows, decisions=decisions)
        collection = {"title": "Player test", "timezone": "America/Los_Angeles",
                      "sources": [], "missing_harnesses": {}, "warnings": []}
        report = Path(self.directory.name) / "report.html"
        report.write_text(report_html(collection, self.events, self.analysis), encoding="utf-8")
        for width in (1280, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(report.as_uri(), wait_until="networkidle")
                replay = next(frame for frame in self.page.frames if frame.url.endswith("/player.html"))
                replay.wait_for_function("document.documentElement.scrollHeight <= innerHeight")
                self.page.locator(".playback-frame").scroll_into_view_if_needed()
                scrollable = replay.locator(".workspace *").evaluate_all("""elements => elements.filter(el =>
                    /^(auto|scroll)$/.test(getComputedStyle(el).overflowY) &&
                    el.scrollHeight > el.clientHeight
                ).map(el => el.matches('.turn-content') ? 'turns' : el.matches('aside') ? 'directions' : el.id || el.className)""")
                self.assertEqual(scrollable, ["turns", "moments"])
                self.assertEqual(replay.locator("aside").evaluate("el => el.scrollTop"), 0)
                replay.evaluate("window.vennPlayback.jumpToMessage('R002')")
                self.assertGreater(replay.locator(".turn-content").evaluate("el => el.scrollTop"), 0)
                self.assertEqual(replay.locator("#agent-text").evaluate("el => el.scrollTop"), 0)
                self.assertTrue(replay.locator(".text-controls").evaluate("""el => {
                    const pane = el.closest('.activity').getBoundingClientRect(), controls = el.getBoundingClientRect();
                    const content = el.nextElementSibling.getBoundingClientRect();
                    return controls.top >= pane.top && controls.bottom <= content.top && content.bottom <= pane.bottom;
                }"""))
                heading_top = replay.locator(".text-controls").bounding_box()["y"]
                replay.locator(".turn-content").evaluate("el => el.scrollTop = el.scrollHeight")
                self.assertEqual(replay.locator(".text-controls").bounding_box()["y"], heading_top)
                self.assertEqual(replay.locator(".activity").evaluate("el => el.scrollTop"), 0)
                keyboard_state = replay.evaluate("window.vennPlayback.state()")
                replay.locator(".turn-content").evaluate("""el => {
                    el.addEventListener('scrollend', () => el.dataset.scrollEnded = 'true', {once:true});
                }""")
                replay.locator(".turn-content").focus()
                replay.locator(".turn-content").press("ArrowUp")
                replay.wait_for_function("document.querySelector('.turn-content').dataset.scrollEnded === 'true'")
                self.assertEqual(replay.evaluate("window.vennPlayback.state()"), keyboard_state)
                state = replay.evaluate("window.vennPlayback.state()")
                region = replay.locator("#moments")
                region.focus()
                heading = region.evaluate("el => el.parentElement.querySelector('h2').getBoundingClientRect().top")
                other_scroll = replay.locator(".turn-content").evaluate("el => el.scrollTop")
                region.evaluate("""el => {
                    delete el.dataset.scrollEnded;
                    el.addEventListener('scrollend', () => el.dataset.scrollEnded = 'true', {once:true});
                }""")
                region.press("ArrowDown")
                replay.wait_for_function("document.getElementById('moments').scrollTop > 0")
                replay.wait_for_function("document.getElementById('moments').dataset.scrollEnded === 'true'")
                self.assertEqual(replay.locator(".turn-content").evaluate("el => el.scrollTop"), other_scroll)
                self.assertEqual(region.evaluate("el => el.parentElement.querySelector('h2').getBoundingClientRect().top"), heading)
                self.assertEqual(replay.evaluate("window.vennPlayback.state()"), state)
                self.assertEqual(replay.locator("aside").evaluate("el => el.scrollTop"), 0)
                before = replay.evaluate("innerHeight")
                replay.locator("#usage-details").evaluate("el => el.open = true")
                replay.wait_for_function("(height) => innerHeight > height", arg=before)
                replay.wait_for_function("document.documentElement.scrollHeight <= innerHeight")
                replay.locator("#usage-details").evaluate("el => el.open = false")
                replay.wait_for_function("(height) => innerHeight === height", arg=before)
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                self.assertFalse(self.errors)

    def test_undated_session_filter_scopes_log_and_message_navigation(self):
        self.open_player([
            ("user", None, "First undated input", "one"),
            ("assistant", None, "First undated reply", "one"),
            ("user", None, "Second undated input", "two"),
            ("assistant", None, "Second undated reply", "two"),
        ])
        self.page.locator("#session").select_option('["Cursor","two"]')
        self.assertEqual(self.page.locator("#event-list button").count(), 2)
        self.assertEqual(self.page.locator("#undated-select option").count(), 2)
        self.assertEqual(self.page.locator("#human-text").inner_text(), "Second undated input")
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Second undated reply")
        self.assertTrue(self.page.locator("#prev-text").is_disabled())
        self.assertTrue(self.page.locator("#next-text").is_disabled())
        self.assertFalse(self.errors)

    def test_manual_navigation_has_no_playback_controls_or_space_shortcut(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 30000, "Response", "one"),
            ("user", start + 3600000, "Last request", "one"),
        ])
        self.assertEqual(self.page.locator("#play, #speed, #skip, #play-state").count(), 0)
        self.page.keyboard.press("Space")
        self.assertEqual(self.state()["now"], start)
        self.page.keyboard.press("ArrowRight")
        self.assertEqual(self.state()["now"], start + 300000)
        self.page.keyboard.press("ArrowDown")
        self.assertEqual(self.page.locator("#human-text").inner_text(), "Last request")
        self.page.locator("#prev-text").click()
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Response")
        self.assertFalse(self.errors)

    def test_session_heading_position_and_arrows_share_one_row(self):
        start = 1794071182211
        self.open_player([(role, start + i * 1000, f"Message {i}", "one")
                          for i, role in enumerate(["user", "assistant"] * 38)])
        self.page.evaluate("window.vennPlayback.jumpToMessage('H021')")
        self.assertEqual(self.page.locator(".text-controls h2").inner_text(), "Session turns")
        self.assertEqual(self.page.locator("#message-number").inner_text(), "11 of 38")
        for width in (1280, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            boxes = self.page.evaluate("""() =>
                ['.text-controls h2','#message-position','.text-arrows'].map(selector => {
                    const {x,y,width,height} = document.querySelector(selector).getBoundingClientRect();
                    return {x,y,width,height};
                })
            """)
            text_bottoms = self.page.evaluate("""() =>
                ['.text-controls h2','#message-number'].map(selector => {
                    const range = document.createRange();
                    range.selectNodeContents(document.querySelector(selector));
                    return range.getBoundingClientRect().bottom;
                })
            """)
            self.assertLessEqual(max(text_bottoms) - min(text_bottoms), 2)
            self.assertLessEqual(boxes[0]["x"] + boxes[0]["width"], boxes[1]["x"])
            self.assertLessEqual(boxes[1]["x"] + boxes[1]["width"], boxes[2]["x"])
            count = self.page.locator("#message-number").bounding_box()
            self.assertLessEqual(count["x"] + count["width"], boxes[1]["x"] + boxes[1]["width"])
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)

    def test_navigation_buttons_show_pressed_feedback_for_mouse_and_keyboard(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 30000, "Response", "one"),
            ("user", start + 60000, "Next request", "one"),
            ("user", start + 120000, "Last request", "one"),
        ], decisions=[{"input": "H001", "title": "First direction", "category": "Scope", "analysis": "Fixture"}])
        for selector in ("#next-text", "#next-idea", "#next"):
            button = self.page.locator(selector)
            button.scroll_into_view_if_needed()
            button.hover()
            button.evaluate("""async el => {
                getComputedStyle(el).backgroundColor;
                await Promise.all(el.getAnimations().map(animation => animation.finished));
            }""")
            color = button.evaluate("(el) => getComputedStyle(el).backgroundColor")
            self.page.mouse.down()
            self.assertNotEqual(button.evaluate("(el) => getComputedStyle(el).backgroundColor"), color)
            self.assertEqual(button.evaluate("(el) => getComputedStyle(el).transform"), "none")
            self.assertEqual(button.evaluate("(el) => getComputedStyle(el).boxShadow"), "none")
            self.page.mouse.up()
            self.page.wait_for_function("""({selector,color}) =>
                getComputedStyle(document.querySelector(selector)).backgroundColor === color
            """, arg={"selector": selector, "color": color})
        previous = self.page.locator("#prev-text")
        previous.focus()
        color = previous.evaluate("(el) => getComputedStyle(el).backgroundColor")
        self.page.keyboard.down("Space")
        self.assertNotEqual(previous.evaluate("(el) => getComputedStyle(el).backgroundColor"), color)
        self.page.keyboard.up("Space")
        self.page.wait_for_function("""color =>
            getComputedStyle(document.getElementById('prev-text')).backgroundColor === color
        """, arg=color)
        self.assertFalse(self.errors)

    def test_navigation_hover_states_use_matching_readable_theme_colors(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First request", "one"),
            ("user", start + 60000, "Next request", "one"),
        ])
        for theme in ("light", "dark"):
            self.page.emulate_media(color_scheme=theme, reduced_motion="reduce")
            self.page.reload(wait_until="networkidle")
            for selector in ("#next", "#next-text"):
                with self.subTest(theme=theme, selector=selector):
                    button = self.page.locator(selector)
                    self.page.mouse.move(0, 0)
                    normal = button.evaluate("""el => {
                        const style = getComputedStyle(el);
                        return {background:style.backgroundColor,color:style.color};
                    }""")
                    button.hover()
                    hover = button.evaluate("""el => {
                        const style = getComputedStyle(el);
                        return {background:style.backgroundColor,color:style.color};
                    }""")
                    self.assertNotEqual(hover["background"], normal["background"])
                    channels = list(map(int, hover["background"].removeprefix("rgb(").removesuffix(")").split(",")))
                    self.assertGreater(max(channels) - min(channels), 50)
                    contrast = button.evaluate("""el => {
                        const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
                        const luminance = color => {
                            ctx.fillStyle = color;ctx.fillRect(0,0,1,1);
                            const rgb = [...ctx.getImageData(0,0,1,1).data].slice(0,3).map(v => {
                                v /= 255;return v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4;
                            });
                            return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
                        };
                        const style = getComputedStyle(el);
                        const a = luminance(style.backgroundColor), b = luminance(style.color);
                        return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                    }""")
                    self.assertGreaterEqual(contrast, 4.5)
                    button.evaluate("el => el.disabled = true")
                    self.assertEqual(button.evaluate("el => getComputedStyle(el).backgroundColor"), normal["background"])
                    button.evaluate("el => el.disabled = false")
        self.assertFalse(self.errors)

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
        self.assertEqual(self.state()["message"], "H003")
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Final response")
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
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Final response")
        self.page.locator("#zoom-reset").click()
        self.assertEqual(self.state()["hi"] - self.state()["lo"], width)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        controls = self.page.locator(".text-controls").bounding_box()
        arrows = self.page.locator(".text-arrows").bounding_box()
        self.assertAlmostEqual(arrows["x"] + arrows["width"], controls["x"] + controls["width"], delta=1)
        self.assertFalse(self.errors)

    def test_message_arrows_follow_timeline_and_pan_across_zoom_boundaries(self):
        start = 1794071182211
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 30000, "First response", "one"),
            ("user", start + 5400000, "Second request", "one"),
            ("assistant", start + 7200000, "Second response", "one"),
        ])

        def assert_cursor(time):
            self.assertEqual(self.state()["now"], time)
            self.assertTrue(self.page.evaluate("""() => {
                const state = window.vennPlayback.state(), svg = document.getElementById('timeline');
                const line = document.getElementById('playhead'), viewport = document.querySelector('.timeline-viewport');
                const expected = 150 + (state.now-state.lo)/(state.hi-state.lo)*(svg.viewBox.baseVal.width-170);
                const cursor = line.getBoundingClientRect(), bounds = viewport.getBoundingClientRect();
                return Math.abs(Number(line.getAttribute('x1'))-expected)<0.01 &&
                    cursor.x >= bounds.left && cursor.x <= bounds.right;
            }"""))

        self.page.locator("#next-text").click()
        assert_cursor(start + 5400000)
        self.assertGreater(self.page.locator(".timeline-viewport").evaluate("(el) => el.scrollLeft"), 0)
        self.page.locator("#prev-text").click()
        assert_cursor(start)
        self.page.locator("#zoom-in").click()
        width = self.state()["hi"] - self.state()["lo"]
        self.page.locator("#next-text").click()
        assert_cursor(start + 5400000)
        self.assertGreater(self.state()["lo"], start)
        self.assertEqual(self.state()["hi"] - self.state()["lo"], width)
        self.page.locator("#prev-text").click()
        assert_cursor(start)
        self.assertEqual(self.state()["lo"], start)
        self.assertFalse(self.errors)

    def test_prompt_arrows_skip_replies_and_anchor_to_response_ownership_in_scope(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First input", "one"),
            ("assistant", start + 1000, "First reply", "one"),
            ("assistant", start + 1500, "Another reply", "one"),
            ("user", start + 2000, "Second input", "one"),
            ("assistant", start + 3000, "Delayed first-turn reply", "one"),
            ("user", start + 4000, "Third input", "one"),
            ("assistant", start + 5000, "Third-turn reply", "one"),
            ("user", start + 6000, "Other session", "two"),
        ], message_fields={
            "H001": {"turn_id": "first"}, "H004": {"turn_id": "second"},
            "R005": {"turn_id": "first"}, "H006": {"turn_id": "third"},
        })
        self.page.locator("#session").select_option('["Cursor","one"]')
        self.page.evaluate("window.vennPlayback.jumpToMessage('R005')")
        self.assertEqual(self.page.locator("#human-text").inner_text(), "First input")
        self.assertEqual(self.page.locator("#message-number").inner_text(), "1 of 3")
        self.assertTrue(self.page.locator("#prev-text").is_disabled())
        self.page.get_by_role("button", name="Next user prompt", exact=True).click()
        self.assertEqual(self.state()["message"], "H004")
        self.page.locator("#next-text").click()
        self.assertEqual(self.state()["message"], "H006")
        self.assertEqual(self.page.locator("#message-number").inner_text(), "3 of 3")
        self.assertTrue(self.page.locator("#next-text").is_disabled())
        self.page.evaluate("window.vennPlayback.jumpToMessage('R007')")
        self.assertTrue(self.page.locator("#next-text").is_disabled())
        self.page.get_by_role("button", name="Previous user prompt", exact=True).click()
        self.assertEqual(self.state()["message"], "H004")
        self.assertFalse(self.errors)

    def test_prompt_arrows_preserve_source_order_for_equal_timestamps(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First input", "one"),
            ("assistant", start, "First reply", "one"),
            ("user", start, "Second input", "one"),
            ("assistant", start, "Second reply", "one"),
        ])
        self.page.evaluate("window.vennPlayback.jumpToMessage('H001')")
        self.page.locator("#next-text").click()
        self.assertEqual(self.state()["message"], "H003")
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Second reply")
        self.assertTrue(self.page.locator("#next-text").is_disabled())
        self.page.locator("#prev-text").click()
        self.assertEqual(self.state()["message"], "H001")
        self.assertTrue(self.page.locator("#prev-text").is_disabled())
        self.assertFalse(self.errors)

    def test_user_and_agent_colors_are_consistent_in_replay_and_full_log(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First input", "one"),
            ("assistant", start + 1000, "First reply", "one"),
            ("user", start + 2000, "Second input", "one"),
            ("assistant", start + 3000, "Second reply", "one"),
            ("user", start + 4000, "Third input", "one"),
            ("user", start + 5000, "Fourth input", "one"),
        ], usages=[{"t": start + 500, "model": "m", "input": 100, "output": 10}], pricing=PRICING)
        url = self.page.url
        for theme in ("light", "dark"):
            for log_only in (False, True):
                with self.subTest(theme=theme, log_only=log_only):
                    self.page.emulate_media(color_scheme=theme)
                    self.page.goto(url + ("?view=log" if log_only else ""), wait_until="networkidle")
                    colors = self.page.evaluate("""() => {
                        const style = selector => getComputedStyle(document.querySelector(selector));
                        const palette = selector => ({
                            background:style(selector).backgroundColor,
                            border:style(selector).borderLeftColor,
                            color:style(selector).color
                        });
                        return {
                            user:palette('#human-text'),footer:palette('#prompt-usage'),
                            userRow:palette('.event-row[data-id="H003"]'),
                            agent:palette('#agent-text'),agentRow:palette('.event-row[data-id="R002"]'),
                            userLegend:style('.legend .human').backgroundColor,
                            agentLegend:style('.legend .agent').backgroundColor,
                            userDot:style('#timeline circle[aria-label^="H003"]').fill,
                            divider:style('.card').borderBottomColor,
                            separators:[...document.querySelectorAll('.event-row')].map(row => {
                                const s = getComputedStyle(row);
                                return [s.borderBottomWidth,s.borderBottomStyle,s.borderBottomColor];
                            })
                        };
                    }""")
                    self.assertEqual(colors["user"], colors["userRow"])
                    self.assertEqual(colors["user"], colors["footer"])
                    self.assertEqual(colors["agent"], colors["agentRow"])
                    self.assertNotEqual(colors["user"]["background"], colors["agent"]["background"])
                    self.assertNotEqual(colors["user"]["border"], colors["agent"]["border"])
                    red, green, blue = map(int, colors["user"]["border"].removeprefix("rgb(").removesuffix(")").split(","))
                    self.assertGreater(green, red)
                    self.assertGreater(green, blue)
                    self.assertEqual(colors["user"]["border"], colors["userLegend"])
                    self.assertEqual(colors["user"]["border"], colors["userDot"])
                    self.assertEqual(colors["agent"]["border"], colors["agentLegend"])
                    self.assertEqual(colors["separators"], [["1px", "solid", colors["divider"]]] * 6)
                    self.page.evaluate("window.vennPlayback.jumpToMessage('R002')")
                    self.assertEqual(self.page.locator(".agent-response.active").evaluate(
                        "el => getComputedStyle(el).borderLeftColor"), colors["agent"]["border"])
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
            width_svg = self.page.locator("#timeline").evaluate("(el) => el.viewBox.baseVal.width")
            x = lambda fraction: box["x"] + (150 + fraction * (width_svg - 170)) / width_svg * box["width"]
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

    def test_inactive_days_can_be_elided_without_changing_event_times(self):
        zone = ZoneInfo("America/Los_Angeles")
        start = round(datetime(2026, 1, 1, tzinfo=zone).timestamp() * 1000)
        day = 24 * 60 * 60 * 1000
        self.open_player([
            ("user", start, "First work day", "one"),
            ("assistant", start + 12 * 60 * 60 * 1000, "First reply", "one"),
            ("user", start + 3 * day, "Next work day", "one"),
            ("assistant", start + 3 * day + 12 * 60 * 60 * 1000, "Next reply", "one"),
        ], usages=[
            {"t": start, "model": "m", "input": 100, "output": 1},
            {"t": start + 3 * day + 12 * 60 * 60 * 1000, "model": "m", "input": 100, "output": 1},
        ])
        compressed_positions = self.page.locator("#timeline circle").evaluate_all(
            "dots => dots.map(dot => Number(dot.getAttribute('cx')))")
        compressed_state = self.state()
        self.assertTrue(self.page.locator("#elide-inactive").is_checked())
        self.assertEqual(compressed_state["elidedDays"], ["2026-01-02", "2026-01-03"])
        self.assertEqual(self.page.locator(".elided-day").count(), 1)
        self.assertTrue(self.page.locator(".time-band").evaluate_all(
            "bands => bands.every(band => Number(band.getAttribute('width')) > 0)"))

        self.page.locator("#elide-inactive").uncheck()
        original_positions = self.page.locator("#timeline circle").evaluate_all(
            "dots => dots.map(dot => Number(dot.getAttribute('cx')))")
        original_state = self.state()
        self.assertEqual(original_state["elidedDays"], [])
        original_gap = original_positions[1] - original_positions[0]
        compressed_gap = compressed_positions[1] - compressed_positions[0]
        self.assertLess(compressed_gap, original_gap * 0.9)
        self.assertEqual(compressed_state["lo"], original_state["lo"])
        self.assertEqual(compressed_state["hi"], original_state["hi"])

        self.page.locator("#elide-inactive").check()
        self.page.evaluate("""() => {
            const svg = document.getElementById('timeline');
            const circle = svg.querySelectorAll('circle')[1];
            const box = svg.getBoundingClientRect();
            svg.dispatchEvent(new MouseEvent('click', {bubbles:true, clientX:box.left +
                Number(circle.getAttribute('cx')) / svg.viewBox.baseVal.width * box.width,
                clientY:box.top + 1}));
        }""")
        self.assertAlmostEqual(self.state()["now"], start + 3 * day, delta=1)
        self.page.locator("#elide-inactive").uncheck()
        self.assertEqual(self.state()["elidedDays"], [])
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
        self.assertEqual(self.page.locator("#human-text").inner_text(), "More")
        self.page.locator("#prev-text").click()
        self.assertEqual(self.state()["message"], "H001")
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Undated reply")
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
        self.assertEqual("Reply to one", self.page.locator("#agent-text .response-text").inner_text())
        self.page.locator("#next-text").click()
        self.assertEqual(self.state()["message"], "H003")
        self.assertNotIn("Reply to one", self.page.locator("#agent-text").inner_text())
        self.assertEqual("Reply to two", self.page.locator("#agent-text .response-text").inner_text())
        self.assertFalse(self.errors)

    def test_prompt_without_response_does_not_borrow_next_turn_reply(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "No response", "one"),
            ("user", start + 1000, "Next input", "one"),
            ("assistant", start + 2000, "Reply to next input", "one"),
        ])
        self.assertEqual(self.page.locator("#agent-text").inner_text(), "No recorded response for this input.")
        self.page.locator("#next-text").click()
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "Reply to next input")

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
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.94")
        self.assertEqual(self.page.locator("#session-spend").count(), 1)
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.assertFalse(self.page.locator("#usage-details").evaluate("(el)=>el.open"))
        self.assertLess(self.page.locator(".usage-card").bounding_box()["height"], 120)
        self.assertEqual(self.page.locator("#model-usage tr").count(), 1)
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.94")
        self.assertEqual(self.page.locator("#session-tokens").inner_text(), "350,000 in · 35,000 out · 3 calls")
        self.assertIn("Models: m", self.page.locator("#session-models").inner_text())
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.26")
        self.assertEqual(self.page.locator("#turn-usage-title").inner_text(), "Turn estimate")
        self.assertIn("Complete recorded turn", self.page.locator("#prompt-usage").get_attribute("title"))
        self.assertIn("inferred", self.page.locator("#turn-attribution").text_content())
        self.assertEqual(self.page.locator("#turn-models").inner_text(), "m")
        prompt = self.page.locator("#human-text").bounding_box()
        footer = self.page.locator("#prompt-usage").bounding_box()
        self.assertGreaterEqual(footer["y"], prompt["y"] + prompt["height"])
        self.assertTrue(self.page.evaluate("document.getElementById('human-text').nextElementSibling.id==='prompt-usage'"))
        first_log = self.page.locator('#event-list [data-id="H001"] .event-usage')
        self.assertIn("m · Est. $0.26 · 100,000 in · 10,000 out", first_log.inner_text())
        self.assertIn("$0.52", self.page.locator('#event-list [data-id="H003"] .event-usage').inner_text())
        self.assertEqual(self.page.locator('#event-list [data-id="R002"] .event-usage').count(), 0)
        self.assertEqual("First response", self.page.locator("#agent-text .response-text").inner_text())
        self.assertEqual(self.page.locator("#turn-usage-rows tr").count(), 3)
        self.assertIn("Unattributed", self.page.locator("#turn-usage-rows").text_content())
        self.assertEqual(self.page.locator("#turn-usage-rows tr").last.locator("td").count(), 6)
        self.page.locator("#timeline").scroll_into_view_if_needed()
        box = self.page.locator("#timeline").bounding_box()
        width_svg = self.page.locator("#timeline").evaluate("(el) => el.viewBox.baseVal.width")
        x = lambda fraction: box["x"] + (150 + fraction * (width_svg - 170)) / width_svg * box["width"]
        y = box["y"] + box["height"] * .55
        self.page.mouse.move(x(.002), y)
        self.page.mouse.down()
        self.page.mouse.move(x(.25), y, steps=8)
        self.page.mouse.up()
        self.assertTrue(self.page.locator("#window-summary").is_visible())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.41")
        self.page.locator("#timeline-scroll").evaluate("(el)=>{el.scrollLeft=el.scrollWidth-el.clientWidth;}")
        self.page.wait_for_function("document.getElementById('window-spend').textContent==='$0.52'")
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.94")
        self.page.locator("#harness").select_option("GitHub Copilot")
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.15")
        self.page.locator("#harness").select_option("Cursor")
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.79")
        self.page.locator("#harness").select_option("all")
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.page.locator("#day").select_option(data["days"][1]["date"])
        self.assertTrue(self.page.locator("#window-summary").is_visible())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "$0.52")
        self.page.locator("#day").select_option("all")
        self.page.evaluate("window.vennPlayback.jumpToMessage('H003')")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.52")
        self.assertIn("200,000 in · 20,000 out", self.page.locator("#turn-tokens").inner_text())
        self.page.locator('#event-list [data-id="H001"]').click()
        self.assertEqual(self.state()["selected"], "H001")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.26")
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
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "$0.26 + unpriced calls")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.26 + unpriced calls")
        self.assertIn("1 / 2 recorded calls priced", self.page.locator("#session-models").inner_text())
        self.assertIn("100,010 in · 10,001 out", self.page.locator("#session-tokens").inner_text())
        self.assertEqual(self.page.locator("#window-spend").inner_text(), "No recorded usage")
        self.assertIn("timing unavailable", self.page.locator("#window-coverage").inner_text())
        self.assertTrue(self.page.locator("#window-summary").is_hidden())
        self.assertIn("$0.26 + unpriced calls", self.page.locator('#event-list [data-id="H001"] .event-usage').inner_text())
        self.assertEqual(self.page.locator('#event-list [data-id="R002"] .event-usage').count(), 0)
        self.assertIsNone(self.page.evaluate("window.pwned"))
        self.open_player(rows, usages)
        self.assertEqual(self.page.locator("#session-spend").inner_text(), "Unpriced")
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "Unpriced")
        self.page.locator("#usage-details > summary").click()
        self.assertIn("Spend is unknown, not zero", self.page.locator("#pricing-note").inner_text())
        self.assertIn("Unpriced", self.page.locator('#event-list [data-id="H001"] .event-usage').inner_text())
        self.assertFalse(self.errors)

    def test_cache_ranges_survive_session_turn_and_filtered_window_displays(self):
        start = 1794071182211
        data = self.open_player([
            ("user", start, "First request", "one"),
            ("user", start + 60000, "Second request", "one"),
        ], [
            {"t": start + 1000, "model": "m", "input": 100000, "output": 1000,
             "cached_read_known": False, "cache_write_known": False},
            {"t": start + 61000, "model": "m", "input": 110000, "output": 1000,
             "cached_read_known": False, "cache_write_known": False},
        ], PRICING)
        self.assertTrue(self.page.locator("#session-spend").inner_text().startswith("Range $"))
        self.assertEqual(data["accounting"]["total"]["priced_calls"], 2)
        self.assertTrue(self.page.locator("#turn-spend").inner_text().startswith("Range $"))
        self.page.evaluate("window.vennPlayback.jumpToMessage('H002')")
        self.assertTrue(self.page.locator("#turn-spend").inner_text().startswith("~$"))
        self.assertIn("(range $", self.page.locator("#turn-spend").inner_text())
        self.assertEqual(data["accounting"]["requests"][1]["estimated_cached_read"], 100000)
        self.page.locator("#zoom-in").click()
        self.assertTrue(self.page.locator("#window-spend").inner_text().startswith(("Range $", "~$")))
        self.assertNotIn("Unpriced", self.page.locator("#model-usage").inner_text())
        self.assertFalse(self.errors)

    def test_message_navigation_reuses_list_and_timeline_without_stale_selection(self):
        start = 1794071182211
        self.open_player([
            ("user", start, "First request", "one"),
            ("assistant", start + 1000, "First response", "one"),
            ("user", start + 60000, "Second request", "one"),
            ("assistant", start + 61000, "Second response", "one"),
        ])
        self.page.evaluate("""() => {
            window.firstRow = document.querySelector('#event-list .event-row');
            window.firstDot = document.querySelector('#timeline circle');
            window.vennPlayback.jumpToMessage('H003');
        }""")
        self.assertEqual(self.page.locator("#human-text").inner_text(), "Second request")
        self.assertEqual(self.page.locator("#event-list .active").get_attribute("data-id"), "H003")
        self.assertTrue(self.page.evaluate("window.firstRow.isConnected && window.firstDot.isConnected"))
        self.assertEqual(self.page.locator('#timeline circle[aria-label^="H003"]').get_attribute("r"), "4.7")
        self.page.locator("#prev-text").click()
        self.assertEqual(self.page.locator("#agent-text .response-text").inner_text(), "First response")
        self.assertEqual(self.page.locator("#event-list .active").get_attribute("data-id"), "H001")
        self.assertEqual(self.page.locator('#timeline circle[aria-label^="H001"]').get_attribute("r"), "4.7")
        self.assertEqual(self.page.locator('#timeline circle[aria-label^="H003"]').get_attribute("r"), "2.8")
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
        self.assertEqual(self.page.locator("#turn-spend").inner_text(), "$0.00")
        self.page.evaluate("window.vennPlayback.jumpToMessage('R001')")
        self.assertTrue(self.page.locator("#prompt-usage").is_hidden())
        self.assertEqual(self.page.locator("#turn-spend").text_content(), "")
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
