"""M2 filename parser tests."""

import unittest

from app.utils.filename_parser import parse_media_filename


class FilenameParserTest(unittest.TestCase):
    """Local filename parsing behavior."""

    def test_parse_movie_with_year_and_noise(self):
        result = parse_media_filename("The.Matrix.1999.1080p.BluRay.x264.mkv")

        self.assertEqual("movie", result.media_type)
        self.assertEqual("The Matrix", result.title)
        self.assertEqual(1999, result.year)
        self.assertIsNone(result.season)
        self.assertIsNone(result.episode)
        self.assertIsNone(result.message)

    def test_parse_episode_with_sxxexx(self):
        result = parse_media_filename("Show.Name.S02E03.2160p.WEB-DL.mkv")

        self.assertEqual("episode", result.media_type)
        self.assertEqual("Show Name", result.title)
        self.assertIsNone(result.year)
        self.assertEqual(2, result.season)
        self.assertEqual(3, result.episode)

    def test_parse_chinese_episode_without_season_defaults_to_one(self):
        result = parse_media_filename("庆余年 第12集 1080p.mp4")

        self.assertEqual("episode", result.media_type)
        self.assertEqual("庆余年", result.title)
        self.assertEqual(1, result.season)
        self.assertEqual(12, result.episode)

    def test_unknown_when_title_is_empty(self):
        result = parse_media_filename("1080p.WEB-DL.x265.mkv")

        self.assertEqual("unknown", result.media_type)
        self.assertEqual("", result.title)
        self.assertIn("无法识别标题", result.message or "")

    def test_parse_movie_ignores_technical_group_before_title(self):
        result = parse_media_filename("(BDRip 1920 FLAC) Inception 2010.mkv")

        self.assertEqual("movie", result.media_type)
        self.assertEqual("Inception", result.title)
        self.assertEqual(2010, result.year)

    def test_parse_movie_ignores_technical_group_between_title_and_year(self):
        result = parse_media_filename("Inception (BDRip 1920 FLAC) 2010.mkv")

        self.assertEqual("movie", result.media_type)
        self.assertEqual("Inception", result.title)
        self.assertEqual(2010, result.year)


if __name__ == "__main__":
    unittest.main()
