"""Select an unused release version from Git tags and PyPI, then update metadata."""
import json
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.error import HTTPError
from urllib.request import urlopen


def release_version(current, tags):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", current):
        raise ValueError("Automatic releases require a major.minor.patch version.")
    major, minor, patch = map(int, current.split("."))
    while f"v{current}" in tags:
        patch += 1
        current = f"{major}.{minor}.{patch}"
    return current


def prepare_release(project, tags):
    text = project.read_text(encoding="utf-8")
    current = tomllib.loads(text)["project"]["version"]
    version = release_version(current, tags)
    if version != current:
        updated, count = re.subn(
            rf'(?m)^version = "{re.escape(current)}"$', f'version = "{version}"', text,
        )
        if count != 1:
            raise ValueError("Expected exactly one project version assignment.")
        project.write_text(updated, encoding="utf-8", newline="\n")
    return version


def published_tags(name):
    try:
        with urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as response:
            return {f"v{version}" for version in json.load(response)["releases"]}
    except HTTPError as error:
        if error.code == 404:
            return set()
        raise


if __name__ == "__main__":
    project = Path("pyproject.toml")
    name = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["name"]
    tags = set(subprocess.check_output(["git", "tag", "--list"], text=True).splitlines())
    print(prepare_release(project, tags | published_tags(name)))
