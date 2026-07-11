"""TVDB 元数据搜索客户端测试。"""

import json
import unittest
from unittest.mock import Mock, patch

from app.schema.media import ParsedMediaName
from app.service.tvdb_client import TVDB_API_BASE_URL, TvdbClient


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class TvdbClientTest(unittest.TestCase):
    """验证 TVDB 官方 API 请求和候选映射。"""

    def test_search_logs_in_and_maps_series_with_episode(self):
        parsed = ParsedMediaName(
            media_type="episode",
            title="Fringe",
            year=None,
            season=2,
            episode=3,
        )
        calls: list[tuple[str, str, dict[str, str], dict[str, object] | None]] = []

        def fake_urlopen(request, timeout=0):
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.get_method(), request.full_url, dict(request.header_items()), body))
            if request.full_url.endswith("/login"):
                return FakeResponse({"status": "success", "data": {"token": "bearer-token"}})
            if "/search?" in request.full_url:
                return FakeResponse(
                    {
                        "status": "success",
                        "data": [
                            {
                                "tvdb_id": "82066",
                                "name": "Fringe",
                                "year": "2008",
                                "overview": "FBI science fiction series.",
                                "image_url": "https://art.example/poster.jpg",
                                "primary_language": "eng",
                                "genres": ["Science Fiction"],
                                "type": "series",
                            }
                        ],
                    }
                )
            if "/series/82066/extended" in request.full_url:
                return FakeResponse(
                    {
                        "status": "success",
                        "data": {
                            "id": 82066,
                            "name": "Fringe",
                            "overview": "Extended overview",
                            "image": "https://art.example/series.jpg",
                            "year": "2008",
                            "originalLanguage": "eng",
                        },
                    }
                )
            if "/series/82066/episodes/default" in request.full_url:
                return FakeResponse(
                    {
                        "status": "success",
                        "data": {
                            "episodes": [
                                {
                                    "id": 456,
                                    "name": "Fracture",
                                    "seasonNumber": 2,
                                    "number": 3,
                                    "overview": "Episode overview",
                                    "aired": "2009-10-01",
                                    "image": "https://art.example/episode.jpg",
                                    "year": "2009",
                                }
                            ]
                        },
                    }
                )
            raise AssertionError(f"unexpected url {request.full_url}")

        with patch("app.service.tvdb_client.url_request.urlopen", side_effect=fake_urlopen):
            candidates = TvdbClient(api_key="tvdb-key", timeout_seconds=9, max_retries=0).search(parsed)

        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("TVDB", candidate.provider)
        self.assertEqual("82066", candidate.provider_id)
        self.assertEqual("episode", candidate.media_type)
        self.assertEqual("Fringe", candidate.title)
        self.assertEqual(2008, candidate.year)
        self.assertEqual(2, candidate.season)
        self.assertEqual(3, candidate.episode)
        self.assertEqual("Fracture", candidate.raw_data["episode_title"])
        self.assertEqual("Episode overview", candidate.overview)
        self.assertIn("season、episode、episode title", candidate.raw_data["match_reason"])
        self.assertEqual("POST", calls[0][0])
        self.assertEqual({"apikey": "tvdb-key"}, calls[0][3])
        self.assertIn(f"{TVDB_API_BASE_URL}/search?", calls[1][1])
        self.assertIn("query=Fringe", calls[1][1])
        self.assertIn("type=series", calls[1][1])
        self.assertIn("Authorization", calls[1][2])

    def test_test_connection_uses_non_user_query(self):
        urls: list[str] = []

        def fake_urlopen(request, timeout=0):
            urls.append(request.full_url)
            if request.full_url.endswith("/login"):
                return FakeResponse({"status": "success", "data": {"token": "bearer-token"}})
            return FakeResponse({"status": "success", "data": []})

        with patch("app.service.tvdb_client.url_request.urlopen", side_effect=fake_urlopen):
            self.assertTrue(TvdbClient(api_key="tvdb-key").test_connection())

        self.assertIn("query=TVDB", urls[1])

    def test_requires_api_key(self):
        with self.assertRaisesRegex(ValueError, "TVDB API Key 未配置"):
            TvdbClient(api_key="").search(
                ParsedMediaName(media_type="episode", title="Fringe", year=None, season=None, episode=None)
            )


if __name__ == "__main__":
    unittest.main()
