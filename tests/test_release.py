import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prepare_release import prepare_release, published_tags, release_version


class ReleaseTests(unittest.TestCase):
    def test_unreleased_version_is_preserved_including_manual_minor_bump(self):
        self.assertEqual(release_version("0.1.1", {"v0.1.0"}), "0.1.1")
        self.assertEqual(release_version("0.2.0", {"v0.1.9", "unrelated"}), "0.2.0")

    def test_released_version_advances_to_first_unused_patch(self):
        self.assertEqual(release_version("0.1.1", {"v0.1.1", "v0.1.2"}), "0.1.3")

    def test_invalid_version_is_rejected(self):
        for value in ("0.1", "0.1.1rc1", "01.1.1", "0.1.1\n"):
            with self.subTest(version=value), self.assertRaises(ValueError):
                release_version(value, set())

    def test_prepare_updates_only_the_version_and_reuses_unreleased_version(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "pyproject.toml"
            original = '[project]\nname = "test"\nversion = "0.1.1"\ndescription = "Unchanged"\n'
            project.write_text(original, encoding="utf-8")
            self.assertEqual(prepare_release(project, {"v0.1.1"}), "0.1.2")
            expected = original.replace('"0.1.1"', '"0.1.2"')
            self.assertEqual(project.read_text(encoding="utf-8"), expected)
            self.assertEqual(prepare_release(project, {"v0.1.1"}), "0.1.2")
            self.assertEqual(project.read_text(encoding="utf-8"), expected)

    def test_pypi_versions_are_reserved_even_without_a_git_release_tag(self):
        with patch("scripts.prepare_release.urlopen", return_value=io.BytesIO(
            b'{"releases": {"0.1.1": [], "0.1.2": []}}'
        )):
            self.assertEqual(release_version("0.1.1", published_tags("test")), "0.1.3")

    def test_missing_pypi_project_is_allowed_but_other_failures_stop_release(self):
        for status in (404, 503):
            with self.subTest(status=status), patch(
                "scripts.prepare_release.urlopen",
                side_effect=HTTPError("https://pypi.org", status, "test", {}, None),
            ):
                if status == 404:
                    self.assertEqual(published_tags("test"), set())
                else:
                    with self.assertRaises(HTTPError):
                        published_tags("test")


if __name__ == "__main__":
    unittest.main()
