"""Check release artifacts without importing the project or reading local histories."""
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


def check(directory):
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution.")

    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = archive.namelist()
        metadata_name = next((name for name in wheel_names if name.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            raise ValueError("The wheel is missing Core Metadata.")
        metadata = archive.read(metadata_name).decode("utf-8")

    with tarfile.open(sources[0]) as archive:
        source_names = [member.name for member in archive.getmembers() if member.isfile()]

    for name in wheel_names + source_names:
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts
                or set(path.parts) & {"private", "raw", "session-report", "agent-session-reports", "tmp", "examples", ".git"}
                or path.suffix in {".jsonl", ".db", ".sqlite", ".pem", ".key"}
                or any(part == ".env" or part.startswith(".env.") for part in path.parts)):
            raise ValueError(f"Unexpected private/history artifact in distribution: {name}")

    runtime_resources = (
        "__init__.py", "__main__.py", "review.py", "sources.py", "agentson.py", "linkedin.py",
        "accounting.py", "retail-pricing.json", "discovery.py", "render.py",
        "assets/player.html", "assets/player.css", "assets/player.js",
        "assets/theme.css", "assets/theme.js", "README.md", "DESIGN.md",
        "agentson-v1.2.json", "agentson-v1.2-entries.json",
        "AGENTSON-SCHEMA-NOTICE.md", "AGENTSON-SCHEMA-LICENSE.txt",
        "prompts/review.md",
        ".github/agents/agent-session-insights.agent.md",
        ".github/skills/collect-sessions/SKILL.md",
        ".github/skills/analyze-sessions/SKILL.md",
        ".github/skills/publish-review/SKILL.md",
    )
    for name in runtime_resources:
        if f"agent_session_insights/{name}" not in wheel_names:
            raise ValueError(f"Missing wheel runtime resource: {name}")
        if not any(path.endswith("/" + name) for path in source_names):
            raise ValueError(f"Missing source distribution resource: {name}")

    source_documents = (
        "LICENSE", "CHANGELOG.md", "SECURITY.md", "README.md", "DESIGN.md",
        "AGENTSON-SCHEMA-LICENSE.txt", "AGENTSON-SCHEMA-NOTICE.md",
    )
    for name in source_documents:
        if not any(path.endswith("/" + name) for path in source_names):
            raise ValueError(f"Missing source distribution document: {name}")

    if "License-Expression: MIT AND Apache-2.0" not in metadata:
        raise ValueError("The wheel metadata must declare both MIT and Apache-2.0 components.")
    if not any(".dist-info/licenses/LICENSE" in name for name in wheel_names):
        raise ValueError("The wheel is missing the MIT application license.")
    if not any(path.endswith("/LICENSE") for path in source_names):
        raise ValueError("The source distribution is missing the MIT application license.")
    if not any(".dist-info/licenses/AGENTSON-SCHEMA-LICENSE.txt" in name for name in wheel_names):
        raise ValueError("The wheel is missing the Apache license for vendored AgentSON schemas.")
    if not any(path.endswith("/AGENTSON-SCHEMA-LICENSE.txt") for path in source_names):
        raise ValueError("The source distribution is missing the Apache license for vendored AgentSON schemas.")
    if any("/" not in name for name in wheel_names):
        raise ValueError("The wheel must not install unnamespaced top-level files.")
    print(f"Checked wheel and source distribution in {directory.resolve()}")


if __name__ == "__main__":
    check(Path("dist"))
