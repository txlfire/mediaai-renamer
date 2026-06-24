"""M2 naming template tests."""

import unittest

from app.schema.media import ParsedMediaName
from app.utils.naming_template import build_preview_name


class NamingTemplateTest(unittest.TestCase):
    """Standard target filename generation."""

    def test_build_movie_name(self):
        parsed = ParsedMediaName("movie", "The Matrix", 1999, None, None)

        self.assertEqual("The.Matrix.1999.mkv", build_preview_name(parsed, ".mkv"))

    def test_build_episode_name(self):
        parsed = ParsedMediaName("episode", "Show Name", None, 2, 3)

        self.assertEqual("Show.Name.S02E03.mp4", build_preview_name(parsed, ".mp4"))

    def test_remove_windows_illegal_characters(self):
        parsed = ParsedMediaName("movie", 'A:B/C*D?', 2020, None, None)

        self.assertEqual("A.B.C.D.2020.mkv", build_preview_name(parsed, ".mkv"))

    def test_unknown_uses_title_only(self):
        parsed = ParsedMediaName("unknown", "Loose Title", None, None, None)

        self.assertEqual("Loose.Title.avi", build_preview_name(parsed, ".avi"))


if __name__ == "__main__":
    unittest.main()
