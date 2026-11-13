"""Exercise an installed distribution outside the checkout, without model calls."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig
import tempfile

import agent_session_insights


def run(command, cwd, env=None):
    result = subprocess.run(
        list(map(str, command)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def installed_files(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    }


def check():
    package = Path(agent_session_insights.__file__).resolve().parent
    before = installed_files(package)
    executable = Path(sysconfig.get_path("scripts")) / (
        "agent-session-insights.exe" if os.name == "nt" else "agent-session-insights"
    )
    if not executable.is_file():
        raise ValueError("The installed console entry point is missing.")
    with tempfile.TemporaryDirectory(prefix="installed-session-review-") as directory:
        root = Path(directory)
        repo = root / "synthetic-project"
        repo.mkdir()
        history = root / "history" / "synthetic-session"
        history.mkdir(parents=True)
        records = [
            {"type": "session.start", "timestamp": "2026-01-01T00:00:00Z",
             "data": {"sessionId": "synthetic-session", "context": {"cwd": str(repo)}}},
            {"type": "user.message", "id": "u1", "timestamp": "2026-01-01T00:00:01Z",
             "data": {"content": "Synthetic installation test; no real project or history."}},
            {"type": "assistant.message", "id": "a1", "timestamp": "2026-01-01T00:00:02Z",
             "data": {"content": "Synthetic response; no historical outcome is claimed."}},
        ]
        (history / "events.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8",
        )
        run([sys.executable, "-I", "-m", "agent_session_insights", "--help"], root)
        args = [executable, "repo", "--repo", repo, "--harness", "copilot", "--root", history.parent]
        run([*args, "--noagent", "--output", root / "direct"], root)

        stub = root / "stub.py"
        stub.write_text(
            "import json\nfrom pathlib import Path\nimport sys\n"
            "assert '--prompt' in sys.argv and '--trust-folder' in sys.argv\n"
            "assert sys.argv[sys.argv.index('--deny-tool') + 1] == 'shell'\n"
            "request = json.loads(Path(sys.argv[-1].split()[1]).read_text(encoding='utf-8'))\n"
            "assert Path(request['reviewer_profile']).is_file()\n"
            "assert all(Path(p).is_file() for p in request['skills'])\n"
            "assert Path('README.md').is_file() and Path('DESIGN.md').is_file()\n"
            "assert all(Path(p).is_file() for p in request['evidence'].values())\n"
            "assert not Path(request['output_directory']).exists()\n"
            "rule = 'write(' + str(Path(request['annotations_path']).relative_to(Path.cwd())) + ')'\n"
            "assert sys.argv[sys.argv.index('--allow-tool') + 1] == rule\n"
            "Path(request['annotations_path']).write_text(json.dumps({\n"
            "  'summary': 'Synthetic installed-package smoke review.',\n"
            "  'decisions': [{'input': 'H001', 'title': 'Synthetic scope',\n"
            "                 'category': 'Scope', 'analysis': 'A test request, not a real project outcome.',\n"
            "                 'response': 'R001'}]}), encoding='utf-8')\n"
            "print('Annotation workspace: ' + str(Path.cwd()))\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = root / "copilot.cmd"
            launcher.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
        else:
            launcher = root / "copilot"
            launcher.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(stub))} "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o700)
        env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", "")}
        text = run([*args, "--output", root / "reviewed"], root, env)
        workspaces = [Path(line.removeprefix("Annotation workspace: "))
                      for line in text.splitlines() if line.startswith("Annotation workspace: ")]
        if len(workspaces) != 1 or workspaces[0].exists() or workspaces[0].is_relative_to(package):
            raise ValueError("The annotation workspace was not temporary and outside the installation.")
        for name in ("direct", "reviewed"):
            output = root / name
            verification = json.loads((output / "verification.json").read_text(encoding="utf-8"))
            if verification["status"] != "passed":
                raise ValueError(f"Installed {name} report failed publication checks.")
            for artifact in ("report.html", "report.pdf", "player.html", "manifest.json", "discovery.json"):
                if not (output / artifact).is_file():
                    raise ValueError(f"Missing installed {name} artifact: {artifact}")
        run([executable, "prepare", "--manifest", root / "reviewed" / "manifest.json",
             "--output", root / "replay"], root)
    if installed_files(package) != before:
        raise ValueError("A report modified the installed package.")
    print("Installed console/module CLI, direct and annotated reports, and frozen replay passed.")


if __name__ == "__main__":
    check()
