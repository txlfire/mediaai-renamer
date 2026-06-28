"""Shared path protocol registry tests."""

import tempfile
import unittest
from pathlib import Path

from app.service.shared_protocols.registry import get_protocol, list_protocol_capabilities


class SharedProtocolRegistryTest(unittest.TestCase):
    def test_registry_returns_m5_protocols(self):
        local = get_protocol("local")
        unc = get_protocol("unc")
        mounted_nfs = get_protocol("mounted_nfs")

        self.assertEqual("local", local.capabilities().protocol)
        self.assertEqual("unc", unc.capabilities().protocol)
        self.assertEqual("mounted_nfs", mounted_nfs.capabilities().protocol)
        self.assertFalse(local.capabilities().supports_credentials)
        self.assertTrue(unc.capabilities().supports_credentials)
        self.assertFalse(mounted_nfs.capabilities().supports_credentials)

    def test_future_protocols_are_listed_as_candidates_only(self):
        capabilities = {item.protocol: item for item in list_protocol_capabilities()}

        for protocol in ("webdav", "ftp", "sftp", "s3"):
            self.assertIn(protocol, capabilities)
            self.assertTrue(capabilities[protocol].future_candidate)
            self.assertFalse(capabilities[protocol].supports_scan)
            self.assertFalse(capabilities[protocol].supports_rename)

        with self.assertRaises(ValueError):
            get_protocol("webdav")

    def test_local_connection_and_directory_listing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Movies").mkdir()
            (root / "poster.jpg").write_text("poster", encoding="utf-8")

            protocol = get_protocol("local")
            result = protocol.test_connection(str(root))
            listing = protocol.list_directories(str(root))

            self.assertTrue(result.success)
            self.assertTrue(result.readable)
            self.assertTrue(result.writable)
            self.assertEqual(["Movies"], [entry.name for entry in listing.entries])
            self.assertTrue(listing.entries[0].readable)
            self.assertTrue(listing.entries[0].writable)

    def test_local_connection_reports_missing_directory(self):
        protocol = get_protocol("local")

        result = protocol.test_connection("Z:/definitely-missing-mediaai")

        self.assertFalse(result.success)
        self.assertIn("不存在", result.message)

    def test_unc_connection_validates_path_shape_without_credentials(self):
        protocol = get_protocol("unc")

        invalid = protocol.test_connection("D:/media")
        valid_shape = protocol.validate_config(r"\\nas\media")
        unavailable = protocol.test_connection(r"\\definitely-missing-mediaai\share")

        self.assertFalse(invalid.success)
        self.assertIn("UNC", invalid.message)
        self.assertTrue(valid_shape.success)
        self.assertFalse(unavailable.success)

    def test_protocols_expose_m5_validation_hooks(self):
        protocol = get_protocol("local")

        self.assertTrue(protocol.validate_config(".").success)
        self.assertTrue(protocol.check_scan_ready(".").success)
        self.assertEqual(str(Path(".").expanduser().resolve()), protocol.normalize_path("."))

        missing_scan = protocol.check_scan_ready("Z:/definitely-missing-mediaai")

        self.assertFalse(missing_scan.success)

    def test_local_rename_readiness_checks_source_and_target_directory(self):
        protocol = get_protocol("local")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "movie.mkv"
            source.write_text("movie", encoding="utf-8")

            ready = protocol.check_rename_ready(str(source), str(root / "renamed.mkv"))
            missing_source = protocol.check_rename_ready(str(root / "missing.mkv"), str(root / "renamed.mkv"))
            missing_target_dir = protocol.check_rename_ready(str(source), str(root / "missing" / "renamed.mkv"))

            self.assertTrue(ready.success)
            self.assertFalse(missing_source.success)
            self.assertFalse(missing_target_dir.success)

    def test_mounted_nfs_lists_directories_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Series").mkdir()
            protocol = get_protocol("mounted_nfs")

            result = protocol.test_connection(str(root))
            listing = protocol.list_directories(str(root))

            self.assertTrue(result.success)
            self.assertTrue(result.readable)
            self.assertTrue(result.writable)
            self.assertIsNotNone(result.suggestion)
            self.assertEqual(["Series"], [entry.name for entry in listing.entries])
            self.assertTrue(listing.entries[0].readable)
            self.assertTrue(listing.entries[0].writable)


if __name__ == "__main__":
    unittest.main()
