import contextlib
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import discover_repository, matches_repository, path_key, select_sessions
from review import analyze, collect, confirm_output, is_junction, main, run_report_harness, write_json

REVIEW = Path(__file__).resolve().parents[1] / "review.py"
TIME = "2026-11-07T17:06:22.211Z"
LATER = "2026-11-08T17:06:22.211Z"


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.homes = {name: self.root / name for name in ("copilot", "claude", "codex", "dsh")}
        self.env = {
            "COPILOT_HOME": str(self.homes["copilot"]),
            "CLAUDE_CONFIG_DIR": str(self.homes["claude"]),
            "CODEX_HOME": str(self.homes["codex"]),
            "DSH_HOME": str(self.homes["dsh"]),
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_junction_detection_without_platform_reparse_tags(self):
        with patch("review.stat", new=object()):
            self.assertFalse(is_junction(self.root))

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_junction_detection_and_overwrite_protection_without_path_is_junction(self):
        output = self.root / "junction-report"
        output.mkdir()
        write_json(output / "collection.json", {"sources": []})
        target = self.root / "junction-target"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("Keep this file.", encoding="utf-8")
        junction = output / "private"
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(junction), str(target)],
            check=True, capture_output=True,
        )
        self.addCleanup(junction.rmdir)
        self.assertFalse(is_junction(target))
        self.assertFalse(is_junction(sentinel))
        self.assertTrue(is_junction(junction))
        with self.assertRaisesRegex(ValueError, "unrelated files or links"):
            confirm_output(output, approved=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "Keep this file.")

    def source(self, harness, session, cwd=None, time=TIME, subagent=False):
        cwd = str(cwd or self.repo)
        if harness == "copilot":
            path = self.homes[harness] / "session-state" / session / "events.jsonl"
            metadata = {
                "type": "session.start", "timestamp": time,
                "data": {"sessionId": session, "context": {"cwd": cwd},
                         "parentSessionId": "parent" if subagent else None},
            }
            records = [metadata, {"type": "user.message", "id": "u1", "timestamp": time,
                                  "data": {"content": "Build the feature"}}]
        elif harness == "codex":
            path = self.homes[harness] / "sessions" / "2026" / f"rollout-{session}.jsonl"
            metadata = {
                "type": "session_meta", "timestamp": time,
                "payload": {"id": session, "cwd": cwd,
                            "source": {"subagent": "review"} if subagent else "cli"},
            }
            records = [metadata, {"type": "response_item", "timestamp": time, "payload": {
                "id": "u1", "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Build the feature"}]}}]
        elif harness == "dsh":
            path = (self.homes[harness] / "sessions" / "--project--" / session
                    / "session.v4.jsonl")
            created = round(datetime.fromisoformat(time.replace("Z", "+00:00")).timestamp() * 1000)
            metadata = {
                "type": "session", "version": 4, "id": session, "createdAt": created,
                "cwd": cwd, "isSeeded": False, "delegationDepth": 1 if subagent else 0,
            }
            if subagent:
                metadata.update(origin="subagent", parentSession="parent")
            records = [metadata, {
                "type": "user/message", "seq": 0, "time": created,
                "data": {"id": "u1", "source": {"kind": "user"},
                         "content": [{"type": "text", "text": "Build the feature"}]},
            }]
        else:
            path = self.homes[harness] / "projects" / "encoded-project" / f"{session}.jsonl"
            metadata = {
                "type": "user", "uuid": "u1", "timestamp": time,
                "sessionId": session, "cwd": cwd, "isSidechain": subagent,
                "message": {"content": "Build the feature"},
            }
            records = [metadata]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        return path

    def command(self, *args):
        output = io.StringIO()
        with (patch.object(sys, "argv", ["review.py", *map(str, args)]),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO())):
            main()
        return output.getvalue()

    def test_report_commands_fail_before_any_work_when_playwright_is_missing(self):
        output = self.root / "existing-report"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("Keep this file.", encoding="utf-8")
        for args in (
            ["repo", "--repo", str(self.repo)],
            ["repo", "--repo", str(self.repo), "--noagent"],
            ["run", "--manifest", str(self.root / "missing-manifest.json")],
        ):
            with self.subTest(args=args):
                stderr = io.StringIO()
                with (patch.dict(sys.modules, {"playwright.sync_api": None}),
                      patch.object(sys, "argv", ["review.py", *args, "--output", str(output)]),
                      patch("review.discover_repository") as discover,
                      patch("review.confirm_output") as confirm,
                      patch("review.run_review") as run,
                      patch("review.run_report_harness") as launch,
                      contextlib.redirect_stderr(stderr),
                      self.assertRaises(SystemExit) as failure):
                    main()
                self.assertEqual(failure.exception.code, 1)
                self.assertIn("Missing report dependency", stderr.getvalue())
                self.assertIn(f'"{sys.executable}" -m pip install', stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
                discover.assert_not_called()
                confirm.assert_not_called()
                run.assert_not_called()
                launch.assert_not_called()
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "Keep this file.")

    def test_report_fails_before_discovery_when_chromium_or_pdf_is_unavailable(self):
        from playwright.sync_api import Error
        for stage in ("launch", "pdf"):
            with self.subTest(stage=stage):
                stderr = io.StringIO()
                with (patch("playwright.sync_api.sync_playwright") as playwright,
                      patch("review.discover_repository") as discover,
                      patch("review.confirm_output") as confirm,
                      patch("review.run_report_harness") as launch,
                      patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo)]),
                      contextlib.redirect_stderr(stderr)):
                    chromium = playwright.return_value.__enter__.return_value.chromium
                    if stage == "launch":
                        chromium.launch.side_effect = Error("Executable doesn't exist")
                    else:
                        chromium.launch.return_value.new_page.return_value.pdf.side_effect = Error("PDF unavailable")
                    with self.assertRaises(SystemExit) as failure:
                        main()
                    if stage == "pdf":
                        chromium.launch.return_value.close.assert_called_once()
                self.assertEqual(failure.exception.code, 1)
                self.assertIn("Chromium/PDF rendering is unavailable", stderr.getvalue())
                self.assertIn(f'"{sys.executable}" -m playwright install chromium', stderr.getvalue())
                discover.assert_not_called()
                confirm.assert_not_called()
                launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "tzdata is required on Windows")
    def test_missing_windows_timezone_dependency_is_reported_before_discovery(self):
        stderr = io.StringIO()
        with (patch.dict(sys.modules, {"tzdata": None}),
              patch("review.discover_repository") as discover,
              patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo)]),
              contextlib.redirect_stderr(stderr),
              self.assertRaises(SystemExit)):
            main()
        self.assertIn("Missing report dependency: tzdata", stderr.getvalue())
        discover.assert_not_called()

    def test_discover_and_prepare_do_not_require_report_dependencies(self):
        source = self.source("copilot", "selected")
        manifest = self.root / "manifest.json"
        write_json(manifest, {"sources": [{"harness": "copilot", "session": "selected", "path": str(source)}]})
        with (patch.dict(sys.modules, {"playwright.sync_api": None}),
              patch("review.check_report_dependencies") as check):
            self.command("discover", "--repo", self.repo)
            self.command("prepare", "--manifest", manifest, "--output", self.root / "prepared")
        check.assert_not_called()
        self.assertTrue((self.root / "prepared" / "collection.json").is_file())

    def test_discovers_native_harnesses_by_metadata_and_ignores_siblings_and_subagents(self):
        for harness in self.homes:
            self.source(harness, harness, self.repo / "src")
            self.source(harness, harness + "-other", str(self.repo) + "-other", time=LATER)
            self.source(harness, harness + "-child", subagent=True, time=LATER)
        result = discover_repository(self.repo)
        self.assertEqual({s["session"] for s in result["sessions"]}, set(self.homes))
        self.assertEqual(result["warnings"], [])
        self.assertTrue(all(s["title"] is None for s in result["sessions"]))
        self.assertNotIn("Build the feature", json.dumps(result))

    def test_dsh_discovery_selects_current_generation_and_reads_compressed_headers(self):
        import zstandard
        path = self.source("dsh", "generation")
        v3 = path.with_name("session.v3.jsonl")
        v3.write_text(json.dumps({
            "type": "session", "version": 3, "id": "generation", "createdAt": 1,
            "cwd": str(self.repo), "isSeeded": False, "delegationDepth": 0,
        }) + "\n", encoding="utf-8")
        compressed = path.with_name("session.v4.jsonl.zstd")
        compressed.write_bytes(zstandard.ZstdCompressor().compress(path.read_bytes()))
        path.unlink()
        result = discover_repository(self.repo, "dsh")
        self.assertEqual([session["session"] for session in result["sessions"]], ["generation"])
        self.assertEqual(Path(result["sessions"][0]["path"]), compressed.resolve())
        self.assertEqual(result["warnings"], [])

    def test_discovery_normalizes_shared_paths_once_per_scan(self):
        for harness in self.homes:
            for i in range(10):
                self.source(harness, f"session-{i}")
        with patch("discovery.path_key", wraps=path_key) as normalize:
            first = discover_repository(self.repo)
            self.assertEqual(len(first["sessions"]), 40)
            self.assertEqual(normalize.call_count, 1)
            second = discover_repository(self.repo)
            self.assertEqual(second, first)
            self.assertEqual(normalize.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_discovery_rechecks_repository_aliases_between_scans(self):
        alias = self.root / "repository-alias"
        other = self.root / "other-project"
        other.mkdir()

        def point_alias(target):
            subprocess.run(
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(target)],
                check=True, capture_output=True,
            )

        point_alias(self.repo)
        self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
        self.source("copilot", "aliased", alias)
        first = discover_repository(self.repo)
        self.assertEqual([s["session"] for s in first["sessions"]], ["aliased"])
        alias.rmdir()
        point_alias(other)
        second = discover_repository(self.repo)
        self.assertEqual(second["sessions"], [])
        self.assertEqual(second["warnings"], [])

    def test_metadata_discovery_stops_before_transcript_and_reports_malformed_metadata(self):
        path = self.source("copilot", "matching")
        with path.open("a", encoding="utf-8") as stream:
            stream.write("Not valid JSON in the conversation tail\n")
        malformed = self.homes["copilot"] / "session-state" / "broken" / "events.jsonl"
        malformed.parent.mkdir()
        malformed.write_text("Not valid JSON\n", encoding="utf-8")
        result = discover_repository(self.repo, "copilot")
        self.assertEqual([s["session"] for s in result["sessions"]], ["matching"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("malformed JSON", result["warnings"][0])

    def test_repository_path_boundaries_case_and_git_root(self):
        repo = r"C:\Source\Project"
        self.assertTrue(matches_repository(repo, {"cwd": r"c:\SOURCE\project\src"}))
        self.assertTrue(matches_repository(repo, {"cwd": "C:/Source/Project/src"}))
        self.assertFalse(matches_repository(repo, {"cwd": r"C:\Source\Project-other"}))
        self.assertFalse(matches_repository(repo, {"cwd": "Project"}))
        self.assertFalse(matches_repository(repo, {"cwd": r"C:\Source\Project\nested",
                                                   "git_root": r"C:\Source\Project\nested"}))
        self.assertTrue(matches_repository("/work/project", {"cwd": "/work/project/src"}))
        self.assertFalse(matches_repository("/work/project", {"cwd": "/work/Project/src"}))

    def test_selection_is_newest_first_with_unknown_dates_last_and_supports_multiple(self):
        self.source("copilot", "old")
        self.source("codex", "new", time=LATER)
        self.source("claude", "unknown", time=None)
        result = discover_repository(self.repo)
        self.assertEqual([s["session"] for s in result["sessions"]], ["new", "old", "unknown"])
        self.assertEqual(select_sessions(result)[0]["session"], "new")
        self.assertEqual(
            [s["session"] for s in select_sessions(result, all_sessions=True)],
            ["new", "old", "unknown"],
        )
        with self.assertRaisesRegex(ValueError, "--all cannot be combined"):
            select_sessions(result, ["new"], all_sessions=True)
        chosen = select_sessions(result, ["copilot:old", "new", "new"])
        self.assertEqual([s["session"] for s in chosen], ["old", "new"])
        with self.assertRaisesRegex(ValueError, "matched 0"):
            select_sessions(result, ["missing"])
        self.source("claude", "old")
        with self.assertRaisesRegex(ValueError, "matched 2"):
            select_sessions(discover_repository(self.repo), ["old"])

    def test_discovers_sessions_through_native_directory_aliases(self):
        alias = self.root / "project-alias"
        try:
            alias.symlink_to(self.repo, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks are unavailable: {error}")
        self.source("copilot", "aliased", alias / "src")
        self.source("codex", "canonical", self.repo.resolve())
        for repository in (self.repo, alias):
            with self.subTest(repository=repository):
                result = discover_repository(repository)
                self.assertEqual({s["session"] for s in result["sessions"]},
                                 {"aliased", "canonical"})

    def test_harness_filter_custom_root_and_archived_codex(self):
        path = self.source("codex", "archived")
        archive = self.homes["codex"] / "archived_sessions"
        archive.mkdir()
        path.rename(archive / path.name)
        self.source("claude", "claude")
        result = discover_repository(self.repo, "codex")
        self.assertEqual([s["session"] for s in result["sessions"]], ["archived"])
        result = discover_repository(self.repo, "codex", archive)
        self.assertEqual([s["session"] for s in result["sessions"]], ["archived"])
        with self.assertRaisesRegex(ValueError, "--root requires --harness"):
            discover_repository(self.repo, root=archive)
        with self.assertRaisesRegex(ValueError, "accessible directory"):
            discover_repository(self.repo, "codex", self.root / "missing")

    def test_missing_logs_and_unsupported_exports_are_explicit(self):
        result = discover_repository(self.repo)
        self.assertEqual(result["sessions"], [])
        self.assertEqual(set(result["unavailable"]), set(self.homes))
        with self.assertRaisesRegex(ValueError, "No repository-matching sessions"):
            select_sessions(result)
        with self.assertRaisesRegex(ValueError, "No repository-matching sessions"):
            select_sessions(result, all_sessions=True)
        with self.assertRaisesRegex(ValueError, "explicitly selected Cursor/Replit export"):
            discover_repository(self.repo, "cursor")

    def test_discover_cli_filters_by_repo_and_keeps_legacy_file_inventory(self):
        path = self.source("copilot", "matching")
        self.source("copilot", "other", str(self.repo) + "-other")
        result = json.loads(self.command("discover", "--repo", self.repo, "--harness", "copilot"))
        self.assertEqual([s["session"] for s in result["sessions"]], ["matching"])
        custom = json.loads(self.command("discover", "--repo", self.repo, "--harness", "copilot",
                                        "--root", path.parent.parent))
        self.assertEqual([s["session"] for s in custom["sessions"]], ["matching"])
        legacy = json.loads(self.command("discover", "--root", path.parent, "--harness", "copilot"))
        self.assertEqual([s["path"] for s in legacy], [str(path.resolve())])

    def test_repo_cli_defaults_to_current_directory_and_persists_frozen_manifest(self):
        self.source("copilot", "old")
        self.source("codex", "new", time=LATER)
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        with patch("render.publish") as publish:
            text = self.command("repo", "--repo", self.repo, "--noagent")
        output = self.root / "agent-session-reports" / self.repo.name
        self.assertEqual(publish.call_args.args[0], output)
        analysis = json.loads((output / "analysis.json").read_text())
        self.assertEqual(analysis["human_inputs"], 1)
        self.assertEqual(set(analysis["harnesses"]), {"codex"})
        self.assertIsNone(analysis["input_tokens"])
        self.assertIn("No reviewed qualitative", analysis["interpretation"]["summary"])
        manifest = json.loads((output / "manifest.json").read_text())
        spec = manifest["sources"][0]
        self.assertEqual((spec["harness"], spec["session"]), ("codex", "new"))
        self.assertTrue((output / spec["path"]).is_file())
        self.assertEqual(len(json.loads((output / "discovery.json").read_text())["sessions"]), 2)
        self.assertIn("Selected codex:new", text)
        with patch("builtins.input", return_value=""), patch("render.publish"), self.assertRaises(SystemExit) as failure:
            self.command("repo", "--repo", self.repo, "--noagent")
        self.assertEqual(failure.exception.code, 1)

    def test_logs_only_archives_raw_selected_logs_without_review_dependencies(self):
        source = self.source("copilot", "selected")
        with source.open("ab") as stream:
            stream.write(b'Not valid JSON; raw export preserves this tail.\r\n')
        expected = source.read_bytes()
        self.source("claude", "unselected", time=LATER)
        output = self.root / "logs"
        with (patch.dict(sys.modules, {"playwright.sync_api": None}),
              patch("review.check_report_dependencies") as check,
              patch("review.collect") as collect_logs,
              patch("review.analyze") as analyze_logs,
              patch("review.run_review") as run,
              patch("review.launch_report_agent") as launch):
            text = self.command("repo", "--repo", self.repo, "--logs-only",
                                "--session", "copilot:selected", "--output", output)
        for operation in (check, collect_logs, analyze_logs, run, launch):
            operation.assert_not_called()
        archive_path = output / "session-logs.zip"
        self.assertIn(str(archive_path), text)
        self.assertEqual(list(output.iterdir()), [archive_path])
        with ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"logs/S01.jsonl", "manifest.json", "discovery.json"})
            manifest = json.loads(archive.read("manifest.json"))
            spec, = manifest["sources"]
            self.assertEqual((spec["harness"], spec["session"]), ("copilot", "selected"))
            self.assertEqual(archive.read(spec["path"]), expected)
            self.assertEqual(spec["sha256"], hashlib.sha256(expected).hexdigest())
            self.assertEqual(spec["bytes"], len(expected))
            self.assertEqual(len(json.loads(archive.read("discovery.json"))["sessions"]), 2)

    def test_logs_only_selection_defaults_filters_and_portable_manifest(self):
        sources = {
            "older": self.source("copilot", "older"),
            "newest": self.source("copilot", "newest", time=LATER),
            "claude": self.source("claude", "claude"),
            "codex": self.source("codex", "codex"),
        }
        self.source("copilot", "unrelated", str(self.repo) + "-other")
        self.source("copilot", "child", subagent=True)
        cases = (
            ([], ["newest"]),
            (["--all"], ["newest", "claude", "codex", "older"]),
            (["--all", "--harness", "copilot",
              "--root", sources["older"].parent.parent], ["newest", "older"]),
            (["--session", "codex:codex", "--session", "copilot:older"], ["codex", "older"]),
        )
        for index, (selection, expected) in enumerate(cases):
            with self.subTest(selection=selection):
                output = self.root / f"logs-{index}"
                self.command("repo", "--repo", self.repo, "--logs-only", "--output", output,
                             "--timezone", "UTC", *selection)
                extracted = self.root / f"extracted-{index}"
                with ZipFile(output / "session-logs.zip") as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual([s["session"] for s in manifest["sources"]], expected)
                    for spec in manifest["sources"]:
                        self.assertEqual(archive.read(spec["path"]), sources[spec["session"]].read_bytes())
                    archive.extractall(extracted)
                prepared = self.root / f"prepared-{index}"
                self.command("prepare", "--manifest", extracted / "manifest.json", "--output", prepared)
                collection = json.loads((prepared / "collection.json").read_text())
                self.assertEqual([s["session"] for s in collection["sources"]], expected)
                self.assertEqual(collection["timezone"], "UTC")

    def test_dsh_logs_only_keeps_compressed_manifest_paths(self):
        import zstandard
        path = self.source("dsh", "compressed")
        compressed = path.with_name("session.v4.jsonl.zstd")
        compressed.write_bytes(zstandard.ZstdCompressor().compress(path.read_bytes()))
        path.unlink()
        output = self.root / "dsh-logs"
        self.command("repo", "--repo", self.repo, "--harness", "dsh", "--logs-only",
                     "--output", output)
        extracted = self.root / "dsh-extracted"
        with ZipFile(output / "session-logs.zip") as archive:
            self.assertIsNone(archive.testzip())
            spec, = json.loads(archive.read("manifest.json"))["sources"]
            self.assertTrue(spec["path"].endswith(".v4.jsonl.zstd"), spec["path"])
            archive.extractall(extracted)
        prepared = self.root / "dsh-prepared"
        self.command("prepare", "--manifest", extracted / "manifest.json", "--output", prepared)
        collection = json.loads((prepared / "collection.json").read_text())
        self.assertEqual([source["harness"] for source in collection["sources"]], ["dsh"])
        self.assertEqual(collection["sources"][0]["counts"]["user"], 1)

    def test_logs_only_default_destination_and_privacy_warning(self):
        self.source("codex", "selected")
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        stderr = io.StringIO()
        with (patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo), "--logs-only"]),
              contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr)):
            main()
        self.assertTrue((self.root / "agent-session-reports" / self.repo.name / "session-logs.zip").is_file())
        self.assertIn("unredacted raw logs", stderr.getvalue())

    def test_logs_only_preserves_destination_files_and_refuses_existing_archive(self):
        self.source("copilot", "selected")
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "report.html"
        sentinel.write_bytes(b"Keep existing report.")
        args = ("repo", "--repo", self.repo, "--logs-only", "--output", output)
        with patch("builtins.input") as prompt:
            self.command(*args)
            archive = output / "session-logs.zip"
            original = archive.read_bytes()
            with self.assertRaises(SystemExit) as failure:
                self.command(*args)
        self.assertEqual(failure.exception.code, 1)
        prompt.assert_not_called()
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(sentinel.read_bytes(), b"Keep existing report.")

    def test_logs_only_removes_partial_archive_when_a_source_changes(self):
        source = self.source("copilot", "selected")
        output = self.root / "changed"
        read_bytes = Path.read_bytes

        def changing_read(path):
            data = read_bytes(path)
            if path == source:
                with source.open("ab") as stream:
                    stream.write(b"changed")
            return data

        stderr = io.StringIO()
        with (patch.object(Path, "read_bytes", changing_read),
              patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo),
                                        "--logs-only", "--output", str(output)]),
              contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr),
              self.assertRaises(SystemExit) as failure):
            main()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn("Source changed while being copied", stderr.getvalue())
        self.assertFalse((output / "session-logs.zip").exists())

    def test_logs_only_no_matches_does_not_create_destination(self):
        output = self.root / "missing-logs"
        with self.assertRaises(SystemExit):
            self.command("repo", "--repo", self.repo, "--logs-only", "--output", output)
        self.assertFalse(output.exists())

    def test_repo_progress_is_flushed_before_discovery_and_generation(self):
        self.source("copilot", "selected")

        class ProgressOutput(io.StringIO):
            flushed = ""

            def flush(self):
                self.flushed = self.getvalue()

        output = ProgressOutput()

        def discover(*args, **kwargs):
            self.assertIn("Finding sessions for", output.flushed)
            return discover_repository(*args, **kwargs)

        def publish(*args):
            self.assertIn("Generating and verifying", output.flushed)

        with (patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo),
                                        "--output", str(self.root / "progress-report"), "--noagent"]),
              contextlib.redirect_stdout(output),
              patch("review.discover_repository", side_effect=discover),
              patch("render.publish", side_effect=publish)):
            main()

    def test_existing_report_requires_confirmation_and_preserves_unrelated_files(self):
        self.source("copilot", "example")
        output = self.root / "overwrite"
        args = ("repo", "--repo", self.repo, "--output", output, "--noagent")
        with patch("render.publish", side_effect=self.publish_fixture):
            self.command(*args)
            original = (output / "collection.json").read_bytes()
            for answer in ("", "n"):
                with patch("builtins.input", return_value=answer), self.assertRaises(SystemExit):
                    self.command(*args)
                self.assertEqual((output / "collection.json").read_bytes(), original)
            with patch("builtins.input", side_effect=EOFError), self.assertRaises(SystemExit):
                self.command(*args)
            self.assertEqual((output / "collection.json").read_bytes(), original)
            with patch("builtins.input", return_value="y") as prompt:
                self.command(*args)
            self.assertIn("[y/N]?", prompt.call_args.args[0])
            self.assertTrue((output / "report.pdf").is_file())
            unrelated = output / "notes.txt"
            unrelated.write_text("Keep me", encoding="utf-8")
            with patch("builtins.input", return_value="y") as prompt, self.assertRaises(SystemExit):
                self.command(*args)
            prompt.assert_not_called()
            self.assertEqual(unrelated.read_text(), "Keep me")

    def test_repo_overwrite_prompt_precedes_discovery_and_defers_removal(self):
        self.source("copilot", "example")
        original_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original_cwd)
        for explicit in (False, True):
            output = self.root / ("explicit" if explicit else "agent-session-reports") / self.repo.name
            args = ("repo", "--repo", self.repo, "--noagent")
            if explicit:
                args += ("--output", output)
            with patch("render.publish", side_effect=self.publish_fixture):
                self.command(*args)
            before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            with (patch("builtins.input", return_value="n") as prompt,
                  patch("review.discover_repository") as discover,
                  patch("review.run_review") as run, patch("review.launch_report_agent") as launch,
                  self.assertRaises(SystemExit)):
                self.command(*args)
            prompt.assert_called_once()
            discover.assert_not_called()
            run.assert_not_called()
            launch.assert_not_called()

            def fail_discovery(*args, **kwargs):
                prompt.assert_called_once()
                self.assertEqual({path.relative_to(output): path.read_bytes()
                                  for path in output.rglob("*") if path.is_file()}, before)
                raise ValueError("Discovery failed")

            with (patch("builtins.input", return_value="y") as prompt,
                  patch("review.discover_repository", side_effect=fail_discovery),
                  self.assertRaises(SystemExit)):
                self.command(*args)
            self.assertEqual({path.relative_to(output): path.read_bytes()
                              for path in output.rglob("*") if path.is_file()}, before)

    def test_approved_overwrite_rechecks_for_unrelated_files_after_discovery(self):
        self.source("copilot", "example")
        output = self.root / "rechecked"
        args = ("repo", "--repo", self.repo, "--output", output, "--noagent")
        with patch("render.publish", side_effect=self.publish_fixture):
            self.command(*args)
        original = (output / "collection.json").read_bytes()

        def discover(*args, **kwargs):
            (output / "notes.txt").write_text("Keep me", encoding="utf-8")
            return discover_repository(*args, **kwargs)

        with (patch("builtins.input", return_value="yes") as prompt,
              patch("review.discover_repository", side_effect=discover), self.assertRaises(SystemExit)):
            self.command(*args)
        prompt.assert_called_once()
        self.assertEqual((output / "collection.json").read_bytes(), original)
        self.assertEqual((output / "notes.txt").read_text(), "Keep me")

    def test_manifest_overwrite_prompt_precedes_loading_and_defers_input_safety_checks(self):
        source = self.source("copilot", "example")
        manifest = self.root / "manifest.json"
        write_json(manifest, {"sources": [{"harness": "copilot", "session": "example", "path": str(source)}]})
        output = self.root / "early-manifest"
        self.command("prepare", "--manifest", manifest, "--output", output)
        original = (output / "collection.json").read_bytes()
        missing = self.root / "missing-manifest.json"
        for answer in ("n", "y"):
            with patch("builtins.input", return_value=answer) as prompt, self.assertRaises(SystemExit):
                self.command("run", "--manifest", missing, "--output", output)
            prompt.assert_called_once()
            self.assertEqual((output / "collection.json").read_bytes(), original)
        frozen = output / "private" / "sources" / "S01.jsonl"
        write_json(manifest, {"sources": [{"harness": "copilot", "session": "example", "path": str(frozen)}]})
        with patch("builtins.input", return_value="y") as prompt, self.assertRaises(SystemExit):
            self.command("run", "--manifest", manifest, "--output", output)
        prompt.assert_called_once()
        self.assertEqual((output / "collection.json").read_bytes(), original)
        self.assertTrue(frozen.is_file())

    def test_manifest_cli_confirms_replacement_but_never_deletes_its_inputs(self):
        source = self.source("copilot", "example")
        manifest = self.root / "manifest.json"
        write_json(manifest, {"sources": [{"harness": "copilot", "session": "example", "path": str(source)}]})
        output = self.root / "draft"
        args = ("prepare", "--manifest", manifest, "--output", output)
        self.command(*args)
        with patch("builtins.input", return_value="yes"):
            self.command(*args)
        frozen = output / "manifest.json"
        write_json(frozen, {"sources": [{"harness": "copilot", "session": "example",
                                       "path": "private/sources/S01.jsonl"}]})
        with patch("builtins.input", return_value="y") as prompt, self.assertRaises(SystemExit):
            self.command("run", "--manifest", frozen, "--output", output)
        prompt.assert_not_called()
        self.assertTrue(frozen.is_file())
        self.assertTrue((output / "private" / "sources" / "S01.jsonl").is_file())

    def test_repo_cli_explicit_harness_multi_selection_and_no_matches(self):
        self.source("copilot", "one")
        self.source("copilot", "two", time=LATER)
        self.source("codex", "newer", time="2026-11-09T00:00:00Z")
        output = self.root / "selected"
        pricing = self.root / "rates.json"
        pricing.write_text(json.dumps({
            "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
            "sources": {}, "models": {},
        }), encoding="utf-8")
        with patch("render.publish"):
            self.command("repo", "--repo", self.repo, "--harness", "copilot",
                         "--session", "one", "--session", "two", "--output", output,
                         "--pricing", pricing, "--timezone", "UTC", "--noagent")
        collection = json.loads((output / "collection.json").read_text())
        self.assertEqual([s["session"] for s in collection["sources"]], ["one", "two"])
        self.assertEqual(collection["timezone"], "UTC")
        self.assertEqual((output / "pricing.json").read_bytes(), pricing.read_bytes())
        self.assertEqual(json.loads((output / "manifest.json").read_text())["pricing"], "pricing.json")
        missing = self.root / "not-created"
        with self.assertRaises(SystemExit):
            self.command("repo", "--repo", self.repo, "--harness", "claude", "--output", missing)
        self.assertFalse(missing.exists())

    def test_all_sessions_cli_includes_all_matches_and_honors_harness_filter(self):
        self.source("copilot", "older")
        self.source("copilot", "newest", time=LATER)
        self.source("claude", "claude")
        self.source("codex", "codex")
        self.source("copilot", "unrelated", str(self.repo) + "-other")
        self.source("copilot", "subagent", subagent=True)
        for harness, expected in (
            (None, ["newest", "claude", "codex", "older"]),
            ("copilot", ["newest", "older"]),
        ):
            with self.subTest(harness=harness):
                output = self.root / f"all-{harness or 'harnesses'}-review"
                args = ["repo", "--repo", self.repo, "--all", "--noagent", "--output", output]
                if harness:
                    args.extend(["--harness", harness])
                self.command(*args)
                collection = json.loads((output / "collection.json").read_text())
                self.assertEqual([s["session"] for s in collection["sources"]], expected)
                analysis = json.loads((output / "analysis.json").read_text())
                self.assertEqual(analysis["conversations"], len(expected))

    def test_all_sessions_cli_rejects_explicit_session_and_empty_inventory(self):
        with patch("review.discover_repository") as discover, self.assertRaises(SystemExit) as failure:
            self.command("repo", "--repo", self.repo, "--all", "--session", "selected")
        self.assertEqual(failure.exception.code, 2)
        discover.assert_not_called()
        output = self.root / "no-matches"
        with patch("review.launch_report_agent") as launch, self.assertRaises(SystemExit) as failure:
            self.command("repo", "--repo", self.repo, "--all", "--output", output)
        self.assertEqual(failure.exception.code, 1)
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_repo_cli_publishes_verified_offline_artifacts(self):
        source = self.source("codex", "example")
        output = self.root / "published"
        result = subprocess.run(
            [sys.executable, str(REVIEW), "repo", "--repo", str(self.repo),
             "--harness", "codex", "--output", str(output), "--noagent"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("report.html", "report.pdf", "player.html", "manifest.json", "discovery.json"):
            self.assertTrue((output / name).is_file(), name)
        self.assertEqual(
            result.stdout.split("Outputs:\n")[1].splitlines(),
            [f"  {output / name}" for name in ("report.html", "report.pdf", "player.html")],
        )
        verification = json.loads((output / "verification.json").read_text())
        self.assertEqual(verification["status"], "passed")
        source.write_text("Live log changed after the report.\n", encoding="utf-8")
        replay = self.root / "prepared-from-snapshots"
        prepared_text = self.command("prepare", "--manifest", output / "manifest.json", "--output", replay)
        self.assertEqual(json.loads((replay / "analysis.json").read_text())["human_inputs"], 1)
        self.assertNotIn("Outputs:", prepared_text)
        rerendered = self.root / "rerendered"
        with patch("render.publish", side_effect=self.publish_fixture):
            run_text = self.command("run", "--manifest", output / "manifest.json", "--output", rerendered)
        self.assertEqual(
            run_text.split("Outputs:\n")[1].splitlines(),
            [f"  {rerendered / name}" for name in ("report.html", "report.pdf", "player.html")],
        )

    def complete_agent_request(self, command, cwd):
        request = json.loads((cwd / command[-1].split()[1]).read_text(encoding="utf-8"))
        self.assertEqual(request["phase"], "annotate")
        self.assertNotEqual(cwd, REVIEW.parent)
        self.assertEqual(cwd, cwd.resolve())
        self.assertTrue(Path(request["reviewer_profile"]).is_file())
        self.assertTrue(all(Path(path).is_file() for path in request["skills"]))
        self.assertTrue((cwd / "README.md").is_file())
        self.assertTrue((cwd / "DESIGN.md").is_file())
        design = (cwd / "DESIGN.md").read_text(encoding="utf-8")
        self.assertIn("Self-contained annotation example", design)
        self.assertNotIn("`examples/creation-annotations.json`", design)
        for path in request["evidence"].values():
            self.assertTrue(Path(path).is_file())
        self.assertFalse(Path(request["output_directory"]).exists())
        write_json(Path(request["annotations_path"]), {
            "summary": "Fixture review of the selected conversation.",
            "decisions": [{"input": "H001", "title": "Feature scope", "category": "Scope",
                           "analysis": "The human requested the feature; completion is unverified."}],
        })
        return request

    def publish_fixture(self, output, *args):
        for name in ("report.html", "report.pdf", "player.html"):
            (output / name).write_text("Fixture artifact", encoding="utf-8")
        write_json(output / "verification.json", {"status": "passed"})

    def test_harness_output_filters_blank_lines_but_preserves_messages_errors_and_exit_status(self):
        command = [sys.executable, "-u", "-c",
                   "import sys; sys.stdout.write('\\n  first message  \\n\\n \\t\\nsecond message\\n'); "
                   "sys.stderr.write('\\nwarning\\n\\n'); sys.stdout.write('last message'); sys.exit(7)"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run_report_harness(command, cwd=self.root)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "  first message  \nsecond message\nwarning\nlast message")
        self.assertEqual(output.getvalue(), "  first message  \nsecond message\nwarning\nlast message\n")

    def test_default_launches_each_harness_with_selected_sources_and_reviewer_prompt(self):
        pricing = self.root / "agent-rates.json"
        write_json(pricing, {
            "currency": "USD", "unit": "per million tokens", "verified_on": "2026-11-07",
            "sources": {}, "models": {},
        })
        for harness in self.homes:
            with self.subTest(harness=harness):
                source = self.source(harness, "selected")
                output = self.root / f"{harness}-report"
                requests = []

                def run(command, cwd):
                    requests.append(self.complete_agent_request(command, cwd))
                    return subprocess.CompletedProcess(command, 0)

                with (patch("review.shutil.which", return_value=f"{harness}.exe"),
                      patch("review.run_report_harness", side_effect=run) as launch,
                      patch("render.publish", side_effect=self.publish_fixture)):
                    text = self.command("repo", "--repo", self.repo, "--harness", harness,
                                        "--output", output, "--timezone", "UTC", "--pricing", pricing)
                command = launch.call_args.args[0]
                self.assertFalse(launch.call_args.kwargs["cwd"].exists())
                self.assertEqual(command[0], str(Path(f"{harness}.exe").resolve()))
                if harness == "copilot":
                    self.assertEqual(command[1:3], ["--agent", "agent-session-insights"])
                    self.assertEqual(command[-2], "--prompt")
                    self.assertIn("--trust-folder", command)
                    self.assertEqual(command[command.index("--deny-tool") + 1], "shell")
                    rule = command[command.index("--allow-tool") + 1]
                    self.assertTrue(rule.startswith("write(session-review-"))
                    self.assertTrue(rule.endswith("annotations.json)"))
                    self.assertNotIn("--allow-all-tools", command)
                    self.assertNotIn("--allow-all", command)
                elif harness == "dsh":
                    self.assertEqual(command[1:5], ["headless", "--permission-mode",
                                                    "workspace-write", "--approval-policy"])
                    self.assertEqual(command[5], "never")
                    self.assertEqual(command[6], command[-1])
                    self.assertIn("annotation-only task", command[-1])
                elif harness == "claude":
                    self.assertEqual(command[1:4], ["--print", "--permission-mode", "dontAsk"])
                    self.assertEqual(command[-2], "--")
                    self.assertEqual(command[command.index("--tools") + 1], "Read,Glob,Grep,Write,Edit")
                    annotation = "./" + Path(requests[0]["annotations_path"]).relative_to(
                        launch.call_args.kwargs["cwd"]
                    ).as_posix()
                    self.assertEqual(
                        command[command.index("--allowedTools") + 1],
                        f"Read,Glob,Grep,Edit({annotation})",
                    )
                else:
                    expected = ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write"]
                    if os.name == "nt":
                        expected.extend(["--enable", "prefer_mxc"])
                    self.assertEqual(command[1:-1], expected)
                request = requests[0]
                self.assertEqual(request["python_executable"], sys.executable)
                self.assertFalse(Path(request["reviewer_profile"]).exists())
                self.assertEqual(len(request["skills"]), 3)
                self.assertIn("Do not rediscover", " ".join(request["instructions"]))
                self.assertFalse(Path(request["manifest_path"]).exists())
                collection = json.loads((output / "collection.json").read_text())
                snapshot = output / collection["sources"][0]["snapshot"]
                self.assertEqual(snapshot.read_bytes(), source.read_bytes())
                self.assertEqual(collection["timezone"], "UTC")
                self.assertEqual((output / "pricing.json").read_bytes(), pricing.read_bytes())
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["annotations"], "annotations.json")
                self.assertEqual(manifest["pricing"], "pricing.json")
                self.assertTrue((output / manifest["sources"][0]["path"]).is_file())
                self.assertEqual(json.loads((output / "annotations.json").read_text())["decisions"][0]["input"], "H001")
                self.assertIn(f"Launching {harness}", text)
                self.assertLess(text.index("Preparing evidence"), text.index(f"Launching {harness}"))
                self.assertLess(text.index(f"Launching {harness}"), text.index("Generating and verifying"))
                self.assertIn("Verified review:", text)
                self.assertEqual(
                    text.split("Outputs:\n")[1].splitlines(),
                    [f"  {output / name}" for name in ("report.html", "report.pdf", "player.html")],
                )

    def test_automatic_launch_uses_first_selected_harness_and_original_cwd_output(self):
        self.source("copilot", "older")
        self.source("claude", "newest", time=LATER)
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        commands = []

        def run(command, cwd):
            commands.append(command)
            request = self.complete_agent_request(command, cwd)
            self.assertEqual(Path(request["output_directory"]),
                             self.root / "agent-session-reports" / self.repo.name)
            return subprocess.CompletedProcess(command, 0)

        with (patch("review.shutil.which", side_effect=lambda name: name),
              patch("review.run_report_harness", side_effect=run),
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo)
        self.assertEqual(commands[0][0], str(Path("claude").resolve()))
        analysis = json.loads(
            (self.root / "agent-session-reports" / self.repo.name / "analysis.json").read_text()
        )
        self.assertEqual(set(analysis["harnesses"]), {"claude"})

    def test_all_sessions_agent_reviews_combined_evidence_with_newest_harness(self):
        self.source("copilot", "older")
        self.source("claude", "newest", time=LATER)
        output = self.root / "combined-agent"

        def run(command, cwd):
            self.assertEqual(command[0], str(Path("claude").resolve()))
            request = self.complete_agent_request(command, cwd)
            prepared = json.loads((Path(request["draft_directory"]) / "analysis.json").read_text())
            self.assertEqual(prepared["conversations"], 2)
            return subprocess.CompletedProcess(command, 0)

        with (patch("review.shutil.which", side_effect=lambda name: name),
              patch("review.run_report_harness", side_effect=run),
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo, "--all", "--output", output)
        collection = json.loads((output / "collection.json").read_text())
        self.assertEqual([s["session"] for s in collection["sources"]], ["newest", "older"])
        self.assertEqual(len(json.loads((output / "manifest.json").read_text())["sources"]), 2)

    def test_missing_harness_does_not_silently_render_or_create_output(self):
        self.source("copilot", "selected")
        output = self.root / "missing-cli"
        with (patch("review.shutil.which", return_value=None),
              patch("review.run_review") as render,
              patch("review.run_report_harness") as launch):
            with self.assertRaises(SystemExit):
                self.command("repo", "--repo", self.repo, "--output", output)
        render.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_failed_or_incomplete_agent_run_is_not_reported_as_verified(self):
        self.source("copilot", "selected")
        output = self.root / "incomplete"
        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.run_report_harness", return_value=subprocess.CompletedProcess(
                  [], 7, stdout="Error: No supported model available")),
              patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo), "--output", str(output)]),
              contextlib.redirect_stdout(io.StringIO()) as stdout,
              contextlib.redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(main(), 7)
        self.assertNotIn("Verified review:", stdout.getvalue())
        self.assertIn("Sign in to Copilot from the CLI", stderr.getvalue())
        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.run_report_harness", return_value=subprocess.CompletedProcess([], 0))):
            with self.assertRaises(SystemExit):
                self.command("repo", "--repo", self.repo, "--output", output)
        output.mkdir()
        (output / "existing.txt").write_text("Keep this file.", encoding="utf-8")
        with patch("review.run_report_harness") as launch, self.assertRaises(SystemExit):
            self.command("repo", "--repo", self.repo, "--output", output)
        launch.assert_not_called()
        self.assertEqual((output / "existing.txt").read_text(), "Keep this file.")

    def test_agent_reports_require_reviewed_annotations_correct_scope_and_intact_snapshots(self):
        self.source("copilot", "selected")
        for defect in ("unreviewed", "wrong-session", "failed-verification", "corrupted-snapshot"):
            with self.subTest(defect=defect):
                output = self.root / defect

                def run(command, cwd):
                    self.complete_agent_request(command, cwd)
                    return subprocess.CompletedProcess(command, 0)

                def publish(output, *args):
                    self.publish_fixture(output)
                    if defect == "unreviewed":
                        path = output / "analysis.json"
                        analysis = json.loads(path.read_text())
                        analysis["interpretation"] = {"summary": "No reviewed qualitative annotations supplied.", "decisions": []}
                        write_json(path, analysis)
                    elif defect == "failed-verification":
                        write_json(output / "verification.json", {"status": "failed"})
                    else:
                        path = output / "collection.json"
                        collection = json.loads(path.read_text())
                        if defect == "wrong-session":
                            collection["sources"][0]["session"] = "another-session"
                            write_json(path, collection)
                        else:
                            (output / collection["sources"][0]["snapshot"]).write_text("Changed snapshot", encoding="utf-8")
                with (patch("review.shutil.which", return_value="copilot.exe"),
                      patch("review.run_report_harness", side_effect=run),
                      patch("render.publish", side_effect=publish),
                      self.assertRaises(SystemExit)):
                    self.command("repo", "--repo", self.repo, "--output", output)

    def test_default_launch_through_native_shim_preserves_request_and_creates_verified_report(self):
        self.source("copilot", "example")
        output = self.root / "review & literal %NAME%"
        stub = self.root / "harness.py"
        stub.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"sys.path.insert(0, {str(REVIEW.parent)!r})\n"
            "from review import write_json\n"
            "request = json.loads(Path(sys.argv[-1].split()[1]).read_text(encoding='utf-8'))\n"
            "assert '--prompt' in sys.argv and '--interactive' not in sys.argv\n"
            "assert '--trust-folder' in sys.argv\n"
            "assert sys.argv[sys.argv.index('--deny-tool') + 1] == 'shell'\n"
            "rule = sys.argv[sys.argv.index('--allow-tool') + 1]\n"
            "assert rule == 'write(' + str(Path(request['annotations_path']).relative_to(Path.cwd())) + ')'\n"
            "assert request['phase'] == 'annotate'\n"
            "assert all(Path(p).is_file() for p in request['evidence'].values())\n"
            "assert not Path(request['output_directory']).exists()\n"
            "write_json(Path(request['annotations_path']), {'summary': 'Stub-reviewed conversation.', 'decisions': []})\n"
            "assert not Path(request['output_directory']).exists()\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            executable = self.root / "harness.cmd"
            executable.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
        else:
            import shlex
            executable = self.root / "harness"
            executable.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(stub))} "$@"\n',
                encoding="utf-8",
            )
            executable.chmod(0o700)
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        with patch("review.shutil.which", return_value=str(Path(".") / executable.name)):
            text = self.command("repo", "--repo", self.repo, "--output", output)
        self.assertIn("Verified review:", text)
        self.assertEqual(json.loads((output / "verification.json").read_text())["status"], "passed")
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["annotations"], "annotations.json")
        self.command("prepare", "--manifest", output / "manifest.json", "--output", self.root / "replay")

    def test_terminal_launches_each_harness_headlessly(self):
        class TerminalOutput(io.StringIO):
            def isatty(self):
                return True

        for harness in self.homes:
            with self.subTest(harness=harness):
                self.source(harness, "selected")
                output = self.root / f"{harness}-terminal"

                def run(command, cwd):
                    self.assertNotIn("--interactive", command)
                    if harness == "copilot":
                        self.assertEqual(command[-2], "--prompt")
                        self.assertIn("--trust-folder", command)
                        self.assertEqual(command[command.index("--deny-tool") + 1], "shell")
                    elif harness == "claude":
                        self.assertEqual(command[1], "--print")
                        self.assertNotIn("--dangerously-skip-permissions", command)
                    elif harness == "dsh":
                        self.assertEqual(command[1], "headless")
                        self.assertEqual(command[command.index("--permission-mode") + 1],
                                         "workspace-write")
                        self.assertEqual(command[command.index("--approval-policy") + 1], "never")
                    else:
                        self.assertEqual(command[1], "exec")
                    self.complete_agent_request(command, cwd)
                    return subprocess.CompletedProcess(command, 0)

                with (patch("review.shutil.which", return_value=f"{harness}.exe"),
                      patch("review.run_report_harness", side_effect=run),
                      patch("render.publish", side_effect=self.publish_fixture),
                      patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo),
                                                "--harness", harness, "--output", str(output)]),
                      patch.object(sys.stdin, "isatty", return_value=True),
                      contextlib.redirect_stdout(TerminalOutput())):
                    main()

    def test_invalid_agent_annotations_fail_before_publication(self):
        self.source("copilot", "selected")
        for kind in ("empty", "malformed", "bad-reference"):
            with self.subTest(kind=kind):
                output = self.root / kind

                def run(command, cwd):
                    request = json.loads((cwd / command[-1].split()[1]).read_text())
                    path = Path(request["annotations_path"])
                    if kind == "malformed":
                        path.write_text("Not JSON", encoding="utf-8")
                    else:
                        write_json(path, {
                            "summary": "" if kind == "empty" else "Fixture review",
                            "decisions": [{"input": "H999", "title": "Invalid", "category": "Scope", "analysis": "No evidence"}],
                        })
                    return subprocess.CompletedProcess(command, 0)

                with (patch("review.shutil.which", return_value="copilot.exe"),
                      patch("review.run_report_harness", side_effect=run),
                      patch("render.publish") as publish,
                      self.assertRaises(SystemExit)):
                    self.command("repo", "--repo", self.repo, "--output", output)
                publish.assert_not_called()

    def test_agent_reviews_frozen_evidence_not_changing_live_source(self):
        source = self.source("copilot", "selected")
        original = source.read_bytes()
        output = self.root / "frozen"

        def run(command, cwd):
            self.complete_agent_request(command, cwd)
            source.write_text("Live log changed after preparation.", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.run_report_harness", side_effect=run),
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo, "--output", output)
        collection = json.loads((output / "collection.json").read_text())
        self.assertEqual((output / collection["sources"][0]["snapshot"]).read_bytes(), original)

    def test_agent_reuses_preparation_and_receives_complete_compact_conversation(self):
        source = self.source("copilot", "selected")
        records = [json.loads(line) for line in source.read_text().splitlines()]
        records[1]["data"]["content"] = "Complete multiline request\nwith code and context."
        records.append({"type": "assistant.message", "id": "reply", "timestamp": TIME,
                        "data": {"content": "Complete assistant response\nincluding its caveats."}})
        records.extend({"type": "session.usage_record", "id": f"usage-{i}", "timestamp": TIME,
                        "data": {"usage": {"model": "gpt-6.1-sol", "inputTokens": 1000, "outputTokens": 100,
                                           "cacheReadTokens": 0, "cacheWriteTokens": 0}}} for i in range(12))
        records.append({"type": "user.message", "id": "approval", "data": {"content": "yes"}})
        source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        output = self.root / "reused"

        def run(command, cwd):
            request = self.complete_agent_request(command, cwd)
            draft = Path(request["draft_directory"])
            events = [json.loads(line) for line in (draft / "events.jsonl").read_text().splitlines()]
            conversation = [json.loads(line) for line in
                            Path(request["evidence"]["conversation.jsonl"]).read_text().splitlines()]
            fields = ("id", "kind", "harness", "session", "source", "timestamp", "text")
            self.assertEqual(conversation, [{key: event[key] for key in fields} for event in events
                                            if event["kind"] in ("user", "assistant")])
            self.assertEqual(conversation[-1]["text"], "yes")
            self.assertIsNone(conversation[-1]["timestamp"])
            coverage = json.loads(Path(request["evidence"]["coverage.json"]).read_text())
            self.assertEqual(coverage["human_inputs"], 2)
            self.assertEqual(coverage["assistant_messages"], 1)
            self.assertEqual(coverage["usage"]["calls"], 12)
            self.assertTrue(coverage["warnings"])
            self.assertNotIn("accounting", coverage)
            self.assertLess(sum(Path(path).stat().st_size for path in request["evidence"].values()),
                            (draft / "events.jsonl").stat().st_size + (draft / "analysis.json").stat().st_size)
            return subprocess.CompletedProcess(command, 0)

        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.run_report_harness", side_effect=run),
              patch("review.collect", wraps=collect) as collect_calls,
              patch("review.analyze", wraps=analyze) as analyze_calls,
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo, "--output", output)
        self.assertEqual(collect_calls.call_count, 1)
        self.assertEqual(analyze_calls.call_count, 1)
        analysis = json.loads((output / "analysis.json").read_text())
        self.assertEqual(analysis["accounting"]["total"]["calls"], 12)
        self.assertEqual(analysis["interpretation"]["decisions"][0]["input"], "H001")

    def test_reused_evidence_is_verified_before_publication(self):
        self.source("copilot", "selected")
        for target in ("events.jsonl", "analysis.json", "collection.json", "pricing.json",
                       "snapshot", "conversation.jsonl", "coverage.json", "unexpected"):
            with self.subTest(target=target):
                output = self.root / f"tampered-{target}"

                def run(command, cwd):
                    request = self.complete_agent_request(command, cwd)
                    draft = Path(request["draft_directory"])
                    if target in request["evidence"]:
                        path = Path(request["evidence"][target])
                    elif target == "snapshot":
                        collection = json.loads((draft / "collection.json").read_text())
                        path = draft / collection["sources"][0]["snapshot"]
                    else:
                        path = draft / target
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write("\n")
                    return subprocess.CompletedProcess(command, 0)

                with (patch("review.shutil.which", return_value="copilot.exe"),
                      patch("review.run_report_harness", side_effect=run),
                      patch("render.publish") as publish,
                      self.assertRaises(SystemExit)):
                    self.command("repo", "--repo", self.repo, "--output", output)
                publish.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
