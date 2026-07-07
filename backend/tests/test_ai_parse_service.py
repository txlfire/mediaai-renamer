"""AI structured parse service tests."""

import tempfile
import unittest
from pathlib import Path

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.schema.media import ParsedMediaName
from app.service.ai_parse_service import parse_media_with_ai
from app.service.external_submission_guard import list_external_submission_blocks
from app.service.settings_service import update_setting_values


class FakeAiParseProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict[str, object]] = []

    def complete_chat(self, messages, max_tokens: int, temperature: float):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return {
            "status": "success",
            "content": self.content,
            "response_ms": 12,
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
        }


class AiParseServiceTest(unittest.TestCase):
    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def enable_ai(self, settings: AppSettings):
        update_setting_values(
            settings,
            {
                "ai.enabled": "true",
                "ai.provider": "deepseek",
                "ai.model": "deepseek-chat",
                "ai.api_key": "sk-secret123456",
                "ai.base_url": "https://api.deepseek.com/v1",
            },
            operator="admin",
        )

    def test_parses_valid_structured_ai_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            self.enable_ai(settings)
            provider = FakeAiParseProvider(
                '{"title":"廉政追缉令","media_type":"tv","year":1997,'
                '"season":1,"episode":17,"confidence":86,"reason":"S01E17 命中剧集格式"}'
            )

            result = parse_media_with_ai(
                settings,
                source_module="rename_preview",
                source_record_id=17,
                file_name="廉政追缉令.S01E17.mp4",
                file_path="/media/廉政追缉令.S01E17.mp4",
                parsed=ParsedMediaName("tv", "廉政追缉令", None, 1, 17),
                provider=provider,
            )

            self.assertEqual("success", result.status)
            self.assertEqual(1, len(result.candidates))
            self.assertEqual("廉政追缉令", result.candidates[0].title)
            self.assertEqual("tv", result.candidates[0].media_type)
            self.assertEqual(1997, result.candidates[0].year)
            self.assertEqual(1, result.candidates[0].season)
            self.assertEqual(17, result.candidates[0].episode)
            self.assertEqual(86, result.candidates[0].confidence)
            self.assertEqual(1, len(provider.calls))
            self.assertIn("只返回 JSON", provider.calls[0]["messages"][0]["content"])
            self.assertNotIn("sk-secret123456", str(provider.calls))

    def test_invalid_ai_response_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            self.enable_ai(settings)
            provider = FakeAiParseProvider("不是 JSON")

            result = parse_media_with_ai(
                settings,
                source_module="rename_preview",
                source_record_id=18,
                file_name="unknown.mp4",
                file_path="/media/unknown.mp4",
                parsed=ParsedMediaName("unknown", "unknown", None, None, None),
                provider=provider,
            )

            self.assertEqual("failed", result.status)
            self.assertEqual([], result.candidates)
            self.assertIn("AI 返回内容不是有效 JSON", result.message)

    def test_sensitive_word_blocks_ai_parse_without_provider_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            self.enable_ai(settings)
            update_setting_values(
                settings,
                {"privacy.custom_sensitive_words": '["LeakedCut"]'},
                operator="admin",
            )
            provider = FakeAiParseProvider(
                '{"title":"Clean","media_type":"movie","confidence":90,"reason":"ok"}'
            )

            result = parse_media_with_ai(
                settings,
                source_module="rename_preview",
                source_record_id=19,
                file_name="LeakedCut.Movie.2024.mp4",
                file_path="/media/LeakedCut.Movie.2024.mp4",
                parsed=ParsedMediaName("movie", "LeakedCut Movie", 2024, None, None),
                provider=provider,
            )
            blocks = list_external_submission_blocks(settings, status="blocked", target_service="ai")

            self.assertEqual("blocked", result.status)
            self.assertEqual([], provider.calls)
            self.assertEqual(1, blocks.total)
            self.assertEqual("ai", blocks.items[0].target_service)

    def test_openai_compatible_profile_can_drive_ai_parse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.build_settings(Path(temp_dir))
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "ai.enabled": "true",
                    "ai.active_profile_id": "openai-main",
                    "ai.provider_profiles": [
                        {
                            "id": "openai-main",
                            "name": "OpenAI Compat",
                            "provider": "openai_compatible",
                            "model": "gpt-4.1-mini",
                            "api_key": "sk-openai123456",
                            "base_url": "https://api.openai-proxy.example/v1",
                            "timeout_ms": 30000,
                            "max_retries": 1,
                            "enabled": True,
                        }
                    ],
                },
                operator="admin",
            )
            provider = FakeAiParseProvider(
                '{"title":"黑客帝国","media_type":"movie","year":1999,'
                '"season":null,"episode":null,"confidence":92,"reason":"命中电影标题和年份"}'
            )

            result = parse_media_with_ai(
                settings,
                source_module="rename_preview",
                source_record_id=20,
                file_name="The.Matrix.1999.1080p.mkv",
                file_path="/media/The.Matrix.1999.1080p.mkv",
                parsed=ParsedMediaName("movie", "The Matrix", 1999, None, None),
                provider=provider,
            )

            self.assertEqual("success", result.status)
            self.assertEqual("黑客帝国", result.candidates[0].title)
            self.assertEqual(1, len(provider.calls))


if __name__ == "__main__":
    unittest.main()
