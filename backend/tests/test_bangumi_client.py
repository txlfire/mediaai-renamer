"""Bangumi 元数据客户端测试。"""

import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from app.schema.media import ParsedMediaName
from app.service.bangumi_client import BangumiClient


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


class BangumiClientTest(unittest.TestCase):
    """验证 Bangumi 请求参数和统一候选映射。"""

    def test_search_maps_anime_subject_to_metadata_candidate(self):
        client = BangumiClient(
            base_url="https://api.bgm.tv",
            access_token="bangumi-token",
            timeout_seconds=12,
            max_retries=2,
            priority=25,
            app_version="0.10.3",
        )
        captured: dict[str, object] = {}

        def fake_post_json(path, query, payload):
            captured["path"] = path
            captured["query"] = query
            captured["payload"] = payload
            return {
                "total": 1,
                "data": [
                    {
                        "id": 400602,
                        "type": 2,
                        "name": "葬送のフリーレン",
                        "name_cn": "葬送的芙莉莲",
                        "date": "2023-09-29",
                        "summary": "勇者一行打倒魔王后的故事。",
                        "images": {"common": "https://lain.bgm.tv/pic/cover.jpg"},
                        "rating": {"score": 8.2},
                        "meta_tags": ["动画"],
                        "tags": [{"name": "奇幻"}, {"name": "冒险"}],
                    }
                ],
            }

        client._post_json = fake_post_json

        candidates = client.search(
            ParsedMediaName(
                media_type="episode",
                title="葬送的芙莉莲",
                year=2023,
                season=1,
                episode=2,
            )
        )

        self.assertEqual(1, len(candidates))
        self.assertEqual("/v0/search/subjects", captured["path"])
        self.assertEqual({"limit": 10, "offset": 0}, captured["query"])
        self.assertEqual(
            {
                "keyword": "葬送的芙莉莲",
                "sort": "match",
                "filter": {"type": [2], "nsfw": False},
            },
            captured["payload"],
        )
        candidate = candidates[0]
        self.assertEqual("Bangumi", candidate.provider)
        self.assertEqual("400602", candidate.provider_id)
        self.assertEqual("episode", candidate.media_type)
        self.assertEqual("葬送的芙莉莲", candidate.title)
        self.assertEqual("葬送のフリーレン", candidate.original_title)
        self.assertEqual("葬送的芙莉莲", candidate.chinese_title)
        self.assertEqual(2023, candidate.year)
        self.assertIsNone(candidate.season)
        self.assertIsNone(candidate.episode)
        self.assertEqual(8.2, candidate.vote_average)
        self.assertEqual("https://lain.bgm.tv/pic/cover.jpg", candidate.poster_path)
        self.assertEqual(["动画", "奇幻", "冒险"], candidate.genres)
        self.assertIn("Bangumi", candidate.raw_data["match_reason"])
        self.assertIn("候选", candidate.raw_data["match_reason"])
        self.assertIn("中文标题", candidate.raw_data["match_reason"])

    def test_search_posts_json_with_user_agent_and_bearer_token(self):
        client = BangumiClient(
            access_token="bangumi-token",
            timeout_seconds=12,
            app_version="0.10.3",
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"total": 0, "data": []})

        with patch("urllib.request.urlopen", fake_urlopen):
            try:
                client.search(ParsedMediaName("episode", "葬送的芙莉莲", 2023, 1, 1))
            except Exception as exc:
                self.fail(f"Bangumi 请求未按预期执行：{exc}")

        request = captured["request"]
        self.assertEqual("POST", request.get_method())
        self.assertEqual(
            "https://api.bgm.tv/v0/search/subjects?limit=10&offset=0",
            request.full_url,
        )
        self.assertEqual(12, captured["timeout"])
        self.assertEqual("Bearer bangumi-token", request.get_header("Authorization"))
        self.assertEqual("application/json", request.get_header("Content-type"))
        self.assertEqual("application/json", request.get_header("Accept"))
        self.assertEqual(
            "txlfire/MediaAI-Renamer/0.10.3 (https://github.com/txlfire/mediaai-renamer)",
            request.get_header("User-agent"),
        )
        self.assertEqual(
            {
                "keyword": "葬送的芙莉莲",
                "sort": "match",
                "filter": {"type": [2], "nsfw": False},
            },
            json.loads(request.data.decode("utf-8")),
        )

    def test_search_retries_one_transient_network_failure(self):
        client = BangumiClient(max_retries=1)
        attempts = 0

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise URLError("temporary timeout")
            return FakeResponse({"total": 0, "data": []})

        with (
            patch("urllib.request.urlopen", fake_urlopen),
            patch("time.sleep") as sleepMock,
        ):
            try:
                client.search(ParsedMediaName("movie", "千与千寻", 2001, None, None))
            except Exception as exc:
                self.fail(f"Bangumi 临时网络错误未正确重试：{exc}")

        self.assertEqual(2, attempts)
        sleepMock.assert_called_once_with(0.25)

    def test_search_reports_auth_failure_without_retry(self):
        client = BangumiClient(access_token="invalid-token", max_retries=2)
        attempts = 0

        def fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, "Bangumi 鉴权失败"):
                client.search(ParsedMediaName("episode", "测试动画", None, 1, 1))

        self.assertEqual(1, attempts)

    def test_search_rejects_invalid_candidate_list_shape(self):
        client = BangumiClient()
        client._post_json = lambda path, query, payload: {"total": 1, "data": {}}

        with self.assertRaisesRegex(RuntimeError, "候选列表格式无效"):
            client.search(ParsedMediaName("episode", "测试动画", None, 1, 1))

    def test_search_discards_nsfw_subject_from_response(self):
        client = BangumiClient()
        client._post_json = lambda path, query, payload: {
            "total": 1,
            "data": [
                {
                    "id": 1,
                    "type": 2,
                    "name": "测试动画",
                    "name_cn": "测试动画",
                    "nsfw": True,
                }
            ],
        }

        candidates = client.search(ParsedMediaName("episode", "测试动画", None, 1, 1))

        self.assertEqual([], candidates)


if __name__ == "__main__":
    unittest.main()
