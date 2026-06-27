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
            self.assertEqual("连接成功", result.message)
            self.assertEqual(["Movies"], [entry.name for entry in listing.entries])

    def test_local_connection_reports_missing_directory(self):
        protocol = get_protocol("local")

        result = protocol.test_connection("Z:/definitely-missing-mediaai")

        self.assertFalse(result.success)
        self.assertIn("不存在", result.message)

    def test_unc_connection_validates_path_shape_without_credentials(self):
        protocol = get_protocol("unc")

        invalid = protocol.test_connection("D:/media")
        valid_shape = protocol.test_connection(r"\\nas\media")

        self.assertFalse(invalid.success)
        self.assertIn("UNC", invalid.message)
        self.assertTrue(valid_shape.success)

    def test_mounted_nfs_lists_directories_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Series").mkdir()
            protocol = get_protocol("mounted_nfs")

            result = protocol.test_connection(str(root))
            listing = protocol.list_directories(str(root))

            self.assertTrue(result.success)
            self.assertEqual(["Series"], [entry.name for entry in listing.entries])


if __name__ == "__main__":
    unittest.main()
