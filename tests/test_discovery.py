import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import discover_repository, matches_repository, select_sessions
from review import main, write_json

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
        self.homes = {name: self.root / name for name in ("copilot", "claude", "codex")}
        self.env = {
            "COPILOT_HOME": str(self.homes["copilot"]),
            "CLAUDE_CONFIG_DIR": str(self.homes["claude"]),
            "CODEX_HOME": str(self.homes["codex"]),
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

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
        os.chdir(self.repo)
        self.addCleanup(os.chdir, original)
        with patch("render.publish") as publish:
            text = self.command("repo", "--repo", self.repo, "--noagent")
        output = self.repo / "session-report"
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
        with patch("render.publish"), self.assertRaises(SystemExit) as failure:
            self.command("repo", "--repo", self.repo, "--noagent")
        self.assertEqual(failure.exception.code, 1)

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
                      patch("review.subprocess.run", side_effect=run) as launch,
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
                elif harness == "claude":
                    self.assertEqual(command[1:4], ["--print", "--permission-mode", "dontAsk"])
                    self.assertEqual(command[-2], "--")
                    self.assertEqual(command[command.index("--tools") + 1], "Read,Glob,Grep,Write,Edit")
                    annotation = "./" + Path(requests[0]["annotations_path"]).relative_to(
                        launch.call_args.kwargs["cwd"]
                    ).as_posix()
                    self.assertEqual(
                        command[command.index("--allowedTools") + 1],
                        f"Read,Glob,Grep,Write({annotation}),Edit({annotation})",
                    )
                else:
                    self.assertEqual(command[1:-1],
                                     ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write"])
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
                self.assertIn("Verified review:", text)
                self.assertEqual(
                    text.split("Outputs:\n")[1].splitlines(),
                    [f"  {output / name}" for name in ("report.html", "report.pdf", "player.html")],
                )

    def test_automatic_launch_uses_first_selected_harness_and_original_cwd_output(self):
        self.source("copilot", "older")
        self.source("claude", "newest", time=LATER)
        original = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, original)
        commands = []

        def run(command, cwd):
            commands.append(command)
            request = self.complete_agent_request(command, cwd)
            self.assertEqual(Path(request["output_directory"]), self.repo / "session-report")
            return subprocess.CompletedProcess(command, 0)

        with (patch("review.shutil.which", side_effect=lambda name: name),
              patch("review.subprocess.run", side_effect=run),
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo)
        self.assertEqual(commands[0][0], str(Path("claude").resolve()))
        analysis = json.loads((self.repo / "session-report" / "analysis.json").read_text())
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
              patch("review.subprocess.run", side_effect=run),
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
              patch("review.subprocess.run") as launch):
            with self.assertRaises(SystemExit):
                self.command("repo", "--repo", self.repo, "--output", output)
        render.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_failed_or_incomplete_agent_run_is_not_reported_as_verified(self):
        self.source("copilot", "selected")
        output = self.root / "incomplete"
        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.subprocess.run", return_value=subprocess.CompletedProcess([], 7)),
              patch.object(sys, "argv", ["review.py", "repo", "--repo", str(self.repo), "--output", str(output)]),
              contextlib.redirect_stdout(io.StringIO()) as stdout,
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(main(), 7)
        self.assertNotIn("Verified review:", stdout.getvalue())
        with (patch("review.shutil.which", return_value="copilot.exe"),
              patch("review.subprocess.run", return_value=subprocess.CompletedProcess([], 0))):
            with self.assertRaises(SystemExit):
                self.command("repo", "--repo", self.repo, "--output", output)
        output.mkdir()
        (output / "existing.txt").write_text("Keep this file.", encoding="utf-8")
        with patch("review.subprocess.run") as launch, self.assertRaises(SystemExit):
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
                      patch("review.subprocess.run", side_effect=run),
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
                    else:
                        self.assertEqual(command[1], "exec")
                    self.complete_agent_request(command, cwd)
                    return subprocess.CompletedProcess(command, 0)

                with (patch("review.shutil.which", return_value=f"{harness}.exe"),
                      patch("review.subprocess.run", side_effect=run),
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
                      patch("review.subprocess.run", side_effect=run),
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
              patch("review.subprocess.run", side_effect=run),
              patch("render.publish", side_effect=self.publish_fixture)):
            self.command("repo", "--repo", self.repo, "--output", output)
        collection = json.loads((output / "collection.json").read_text())
        self.assertEqual((output / collection["sources"][0]["snapshot"]).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
