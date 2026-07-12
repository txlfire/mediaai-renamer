"""豆瓣代理元数据客户端测试。"""

import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from app.schema.media import ParsedMediaName
from app.service.douban_proxy_client import DoubanProxyClient


class FakeResponse:
    """提供 urllib 上下文管理器兼容的内存响应。"""

    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class DoubanProxyClientTest(unittest.TestCase):
    """验证豆瓣代理协议请求和候选映射。"""

    def test_search_gets_proxy_and_maps_candidate(self):
        client = DoubanProxyClient(
            base_url="https://douban.example.test/api",
            api_key="proxy-token",
            timeout_seconds=12,
            max_retries=1,
            priority=50,
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "items": [
                        {
                            "id": "1292052",
                            "type": "movie",
                            "title": "肖申克的救赎",
                            "original_title": "The Shawshank Redemption",
                            "year": 1994,
                            "summary": "希望让人自由。",
                            "rating": 9.7,
                            "poster": "https://img.example.test/poster.jpg",
                            "language": "en",
                            "genres": ["剧情", "犯罪"],
                            "cast": ["蒂姆·罗宾斯"],
                            "directors": ["弗兰克·德拉邦特"],
                            "imdb_id": "tt0111161",
                        }
                    ]
                }
            )

        with patch("urllib.request.urlopen", fake_urlopen):
            candidates = client.search(ParsedMediaName("movie", "肖申克的救赎", 1994, None, None))

        self.assertEqual(1, len(candidates))
        request = captured["request"]
        self.assertEqual("GET", request.get_method())
        self.assertIn("https://douban.example.test/api/search?", request.full_url)
        self.assertIn("query=%E8%82%96%E7%94%B3%E5%85%8B%E7%9A%84%E6%95%91%E8%B5%8E", request.full_url)
        self.assertIn("media_type=movie", request.full_url)
        self.assertIn("year=1994", request.full_url)
        self.assertEqual(12, captured["timeout"])
        self.assertEqual("Bearer proxy-token", request.get_header("Authorization"))
        self.assertEqual("application/json", request.get_header("Accept"))
        candidate = candidates[0]
        self.assertEqual("豆瓣代理", candidate.provider)
        self.assertEqual("1292052", candidate.provider_id)
        self.assertEqual("movie", candidate.media_type)
        self.assertEqual("肖申克的救赎", candidate.title)
        self.assertEqual("The Shawshank Redemption", candidate.original_title)
        self.assertEqual(1994, candidate.year)
        self.assertEqual(9.7, candidate.vote_average)
        self.assertEqual("tt0111161", candidate.imdb_id)
        self.assertEqual(["剧情", "犯罪"], candidate.genres)
        self.assertEqual(["蒂姆·罗宾斯"], candidate.cast)
        self.assertEqual(["弗兰克·德拉邦特"], candidate.directors)
        self.assertIn("豆瓣代理", candidate.raw_data["match_reason"])

    def test_search_accepts_data_list_shape(self):
        client = DoubanProxyClient(base_url="https://douban.example.test")
        client._get_json = lambda path, query: {
            "data": [
                {
                    "douban_id": "30295905",
                    "media_type": "episode",
                    "chinese_title": "庆余年",
                    "english_title": "Joy of Life",
                    "year": "2019",
                    "season": 1,
                    "episode": 2,
                }
            ]
        }

        candidates = client.search(ParsedMediaName("episode", "庆余年", 2019, 1, 2))

        self.assertEqual(1, len(candidates))
        self.assertEqual("30295905", candidates[0].provider_id)
        self.assertEqual("庆余年", candidates[0].title)
        self.assertEqual("Joy of Life", candidates[0].english_title)
        self.assertEqual(1, candidates[0].season)
        self.assertEqual(2, candidates[0].episode)

    def test_search_rejects_missing_base_url(self):
        client = DoubanProxyClient(base_url="")

        with self.assertRaisesRegex(ValueError, "豆瓣代理 Base URL 未配置"):
            client.search(ParsedMediaName("movie", "测试", None, None, None))

    def test_search_reports_auth_failure_without_retry(self):
        client = DoubanProxyClient(base_url="https://douban.example.test", api_key="bad", max_retries=2)
        attempts = 0

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, "豆瓣代理鉴权失败"):
                client.search(ParsedMediaName("movie", "测试", None, None, None))

        self.assertEqual(1, attempts)

    def test_search_retries_one_transient_network_failure(self):
        client = DoubanProxyClient(base_url="https://douban.example.test", max_retries=1)
        attempts = 0

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise URLError("temporary timeout")
            return FakeResponse({"items": []})

        with (
            patch("urllib.request.urlopen", fake_urlopen),
            patch("time.sleep") as sleepMock,
        ):
            candidates = client.search(ParsedMediaName("movie", "测试", None, None, None))

        self.assertEqual([], candidates)
        self.assertEqual(2, attempts)
        sleepMock.assert_called_once_with(0.25)

    def test_test_connection_uses_non_user_query(self):
        client = DoubanProxyClient(base_url="https://douban.example.test")
        captured: dict[str, object] = {}

        def fake_get_json(path, query):
            captured["path"] = path
            captured["query"] = query
            return {"items": []}

        client._get_json = fake_get_json

        self.assertTrue(client.test_connection())
        self.assertEqual("/search", captured["path"])
        self.assertEqual({"query": "douban", "media_type": "movie", "limit": 1}, captured["query"])


if __name__ == "__main__":
    unittest.main()
