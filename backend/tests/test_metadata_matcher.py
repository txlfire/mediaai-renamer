"""Metadata match scoring tests."""

import unittest

from app.schema.media import ParsedMediaName
from app.schema.metadata import MetadataCandidate
from app.service.metadata_matcher import score_metadata_candidate


class MetadataMatcherTest(unittest.TestCase):
    """Weighted metadata scoring behavior."""

    def test_movie_exact_title_year_and_type_scores_full_points(self):
        local = ParsedMediaName("movie", "The Matrix", 1999, None, None)
        candidate = MetadataCandidate(
            provider="TMDB",
            provider_id="603",
            media_type="movie",
            title="黑客帝国",
            original_title="The Matrix",
            year=1999,
            season=None,
            episode=None,
            overview="",
        )

        result = score_metadata_candidate(local, candidate)

        self.assertEqual(100, result.score)
        self.assertEqual("high_confidence", result.status)

    def test_year_one_year_apart_gets_partial_year_score(self):
        local = ParsedMediaName("movie", "The Matrix", 2000, None, None)
        candidate = MetadataCandidate(
            provider="TMDB",
            provider_id="603",
            media_type="movie",
            title="The Matrix",
            original_title="",
            year=1999,
            season=None,
            episode=None,
            overview="",
        )

        result = score_metadata_candidate(local, candidate)

        self.assertEqual(95, result.score)
        self.assertEqual("high_confidence", result.status)

    def test_type_mismatch_scores_zero_to_prevent_cross_media_match(self):
        local = ParsedMediaName("movie", "The Matrix", 1999, None, None)
        candidate = MetadataCandidate(
            provider="TMDB",
            provider_id="1399",
            media_type="episode",
            title="The Matrix",
            original_title="",
            year=1999,
            season=1,
            episode=1,
            overview="",
        )

        result = score_metadata_candidate(local, candidate)

        self.assertEqual(0, result.score)
        self.assertEqual("failed", result.status)

    def test_episode_requires_matching_season_and_episode_for_episode_points(self):
        local = ParsedMediaName("episode", "Show Name", None, 2, 3)
        candidate = MetadataCandidate(
            provider="TMDB",
            provider_id="episode-1",
            media_type="episode",
            title="Show Name",
            original_title="",
            year=None,
            season=2,
            episode=4,
            overview="",
        )

        result = score_metadata_candidate(local, candidate)

        self.assertEqual(70, result.score)
        self.assertEqual("low_confidence", result.status)

    def test_weak_title_match_maps_to_failed_status(self):
        local = ParsedMediaName("movie", "Completely Different", 1999, None, None)
        candidate = MetadataCandidate(
            provider="TMDB",
            provider_id="603",
            media_type="movie",
            title="The Matrix",
            original_title="",
            year=2023,
            season=None,
            episode=None,
            overview="",
        )

        result = score_metadata_candidate(local, candidate)

        self.assertLess(result.score, 30)
        self.assertEqual("failed", result.status)


if __name__ == "__main__":
    unittest.main()
