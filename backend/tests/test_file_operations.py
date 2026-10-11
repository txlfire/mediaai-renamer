"""禁止覆盖移动的平台边界回归，真实文件仅位于临时目录。"""

import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.utils.file_operations import moveFileNoReplace, copyFileNoReplace


class FileOperationsTest(unittest.TestCase):
    def test_native_move_preserves_source_content(self):
        with tempfile.TemporaryDirectory() as tempDir:
            source = Path(tempDir) / "source.mkv"
            target = Path(tempDir) / "target.mkv"
            source.write_bytes(b"source")
            moveFileNoReplace(source, target)
            self.assertFalse(source.exists())
            self.assertEqual(b"source", target.read_bytes())

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tempDir:
            source = Path(tempDir) / "source.mkv"
            target = Path(tempDir) / "target.mkv"
            source.write_bytes(b"source")
            target.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                moveFileNoReplace(source, target)
            self.assertEqual(b"source", source.read_bytes())
            self.assertEqual(b"existing", target.read_bytes())

    def test_copy_refuses_existing_target_before_writing(self):
        with tempfile.TemporaryDirectory() as tempDir:
            source = Path(tempDir) / "source.mkv"
            target = Path(tempDir) / "target.mkv"
            source.write_bytes(b"source")
            target.write_bytes(b"existing")
            info = source.stat()
            with self.assertRaises(FileExistsError):
                copyFileNoReplace(source, target, [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
            self.assertEqual(b"source", source.read_bytes())
            self.assertEqual(b"existing", target.read_bytes())

    def test_linux_uses_noreplace_flag_without_fallback(self):
        native = MagicMock()
        native.renameat2.return_value = -1
        with (
            patch("app.utils.file_operations.os.name", "posix"),
            patch("app.utils.file_operations.sys.platform", "linux"),
            patch("app.utils.file_operations.ctypes.CDLL", return_value=native),
            patch("app.utils.file_operations.ctypes.get_errno", return_value=errno.EEXIST),
            patch("app.utils.file_operations.os.rename") as fallback,
        ):
            with self.assertRaises(FileExistsError):
                moveFileNoReplace("source.mkv", "target.mkv")
        self.assertEqual(1, native.renameat2.call_args.args[4])
        fallback.assert_not_called()

    def test_unsupported_platform_refuses_before_moving(self):
        with (
            patch("app.utils.file_operations.os.name", "posix"),
            patch("app.utils.file_operations.sys.platform", "darwin"),
            patch("app.utils.file_operations.os.rename") as mover,
        ):
            with self.assertRaises(OSError) as error:
                moveFileNoReplace("source.mkv", "target.mkv")
        self.assertEqual(errno.ENOTSUP, error.exception.errno)
        mover.assert_not_called()
