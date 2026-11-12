"""Stage only reviewed public assets; never copy raw snapshots or repository files."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources import SECRET, SHARED_SESSION_URL

PUBLIC_FILES = (
    "report.html", "report.pdf", "player.html", "timeline.png", "analysis.json",
    "events.jsonl", "collection.json", "verification.json", "pricing.json",
)


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Pages output must be empty.")
    verification = json.loads((source / "verification.json").read_text())
    base = verification.get("pdf_link_base_url") or ""
    if verification.get("status") != "passed" or not base.startswith("https://"):
        raise ValueError("Generate and verify the review with --public-base-url before publishing.")
    for name in PUBLIC_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or linked public asset: {name}")
        if path.suffix in (".html", ".json", ".jsonl"):
            text = path.read_text(encoding="utf-8")
            if SECRET.search(text) or SHARED_SESSION_URL.search(text):
                raise ValueError(f"Potential credential or unreviewed sharing link in {name}; review before publishing.")
    links = re.findall(rb"/URI\s*\(([^)]*)\)", (source / "report.pdf").read_bytes())
    if not any(link.startswith(base.encode()) for link in links) or any(
            link.lower().startswith(b"file:") for link in links):
        raise ValueError("Public PDF links are missing or still point to local files.")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in PUBLIC_FILES:
        shutil.copyfile(source / name, output / name)
    shutil.copyfile(source / "report.html", output / "index.html")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Staged reviewed public demo: {prepare(args.source, args.output)}")
