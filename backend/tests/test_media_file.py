"""媒体文件工具测试。"""

import unittest

from app.core.config import ScanSettings
from app.utils.media_file import is_video_file


class MediaFileUtilsTest(unittest.TestCase):
    """媒体文件识别和扫描配置测试。"""

    def test_is_video_file_accepts_common_extensions_case_insensitive(self):
        """常见视频扩展名应被识别，且大小写不敏感。"""

        self.assertTrue(is_video_file("Movie.MKV"))
        self.assertTrue(is_video_file("episode.rmvb"))
        self.assertTrue(is_video_file("clip.m2ts"))

    def test_is_video_file_rejects_non_video_files(self):
        """非视频文件不应被识别为视频。"""

        self.assertFalse(is_video_file("poster.jpg"))
        self.assertFalse(is_video_file("subtitle.ass"))
        self.assertFalse(is_video_file("README"))

    def test_scan_settings_has_safe_defaults(self):
        """扫描配置默认使用 100 个文件一批，批间隔 1 秒。"""

        settings = ScanSettings()

        self.assertEqual(100, settings.batch_size)
        self.assertEqual(1, settings.batch_interval_seconds)

    def test_scan_settings_rejects_invalid_values(self):
        """扫描批大小必须大于 0，批间隔不得小于 0。"""

        with self.assertRaises(ValueError):
            ScanSettings(batch_size=0)
        with self.assertRaises(ValueError):
            ScanSettings(batch_interval_seconds=-1)


if __name__ == "__main__":
    unittest.main()
