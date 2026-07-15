"""Shared path protocol registry tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from app.service.shared_protocols.base import RemoteProtocolCapability, SharedPathContext
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

        for protocol in ("ftp", "sftp", "s3"):
            self.assertIn(protocol, capabilities)
            self.assertTrue(capabilities[protocol].future_candidate)
            self.assertFalse(capabilities[protocol].supports_scan)
            self.assertFalse(capabilities[protocol].supports_rename)
            self.assertIn(RemoteProtocolCapability.BROWSE.value, capabilities[protocol].remote_capabilities)
            self.assertIn(RemoteProtocolCapability.READ_METADATA.value, capabilities[protocol].remote_capabilities)

        with self.assertRaises(ValueError):
            get_protocol("ftp")

    def test_webdav_protocol_validates_https_and_lists_directories(self):
        capabilities = {item.protocol: item for item in list_protocol_capabilities()}
        protocol = get_protocol("webdav")

        self.assertIn("webdav", capabilities)
        self.assertFalse(capabilities["webdav"].future_candidate)
        self.assertTrue(capabilities["webdav"].supports_credentials)
        self.assertTrue(capabilities["webdav"].supports_directory_browse)
        self.assertTrue(capabilities["webdav"].supports_scan)
        self.assertFalse(protocol.validate_config("http://nas.example/dav").success)
        self.assertTrue(protocol.validate_config("https://nas.example/dav").success)

        class FakeResponse:
            status = 207

            def __init__(self, body: str):
                self.body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.body

        propfind_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
          </d:response>
          <d:response>
            <d:href>/dav/Movies/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
          </d:response>
          <d:response>
            <d:href>/dav/poster.jpg</d:href>
            <d:propstat><d:prop><d:resourcetype /></d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """

        with patch("app.service.shared_protocols.webdav.urlopen", return_value=FakeResponse(propfind_xml)):
            result = protocol.test_connection(
                "https://nas.example/dav/",
                SharedPathContext(path_type="webdav", username="user", secret="password", has_secret=True),
            )
            listing = protocol.list_directories(
                "https://nas.example/dav/",
                SharedPathContext(path_type="webdav", username="user", secret="password", has_secret=True),
            )

        self.assertTrue(result.success)
        self.assertEqual(["Movies"], [entry.name for entry in listing.entries])
        self.assertEqual("https://nas.example/dav/Movies/", listing.entries[0].path)

    def test_webdav_protocol_lists_files_with_etag_metadata(self):
        protocol = get_protocol("webdav")

        class FakeResponse:
            status = 207

            def __init__(self, body: str):
                self.body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.body

        root_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
          </d:response>
          <d:response>
            <d:href>/dav/Movies/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
          </d:response>
          <d:response>
            <d:href>/dav/root.mp4</d:href>
            <d:propstat><d:prop>
              <d:resourcetype />
              <d:getcontentlength>2048</d:getcontentlength>
              <d:getlastmodified>Tue, 14 Jul 2026 01:02:03 GMT</d:getlastmodified>
              <d:getetag>"root-etag"</d:getetag>
            </d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """
        movies_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/Movies/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection /></d:resourcetype></d:prop></d:propstat>
          </d:response>
          <d:response>
            <d:href>/dav/Movies/电影.mkv</d:href>
            <d:propstat><d:prop>
              <d:resourcetype />
              <d:getcontentlength>4096</d:getcontentlength>
              <d:getlastmodified>Tue, 14 Jul 2026 02:03:04 GMT</d:getlastmodified>
              <d:getetag>"movie-etag"</d:getetag>
            </d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """

        def fake_urlopen(request, timeout=5):
            if request.full_url.endswith("/Movies/"):
                return FakeResponse(movies_xml)
            return FakeResponse(root_xml)

        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=fake_urlopen):
            files = protocol.list_files("https://nas.example/dav/")

        self.assertEqual(["电影.mkv", "root.mp4"], [item.name for item in files])
        self.assertEqual([4096, 2048], [item.file_size for item in files])
        self.assertEqual(['"movie-etag"', '"root-etag"'], [item.version for item in files])

    def test_webdav_rename_readiness_accepts_existing_source_and_missing_target(self):
        protocol = get_protocol("webdav")

        class FakeResponse:
            status = 207

            def __init__(self, body: str):
                self.body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.body

        source_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/Movie.2024.1080p.mkv</d:href>
            <d:propstat><d:prop>
              <d:resourcetype />
              <d:getcontentlength>2048</d:getcontentlength>
              <d:getetag>"source-etag"</d:getetag>
            </d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """

        def fake_urlopen(request, timeout=5):
            if request.full_url.endswith("/Movie.Safe.mkv"):
                raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)
            return FakeResponse(source_xml)

        with patch("app.service.shared_protocols.webdav.urlopen", side_effect=fake_urlopen):
            result = protocol.check_rename_ready(
                "https://nas.example/dav/Movie.2024.1080p.mkv",
                "https://nas.example/dav/Movie.Safe.mkv",
                SharedPathContext(path_type="webdav", username="user", secret="password", has_secret=True),
            )

        self.assertTrue(result.success)
        self.assertTrue(result.readable)
        self.assertTrue(result.writable)
        self.assertIn("dry-run", result.message)

    def test_webdav_rename_readiness_rejects_existing_target(self):
        protocol = get_protocol("webdav")

        class FakeResponse:
            status = 207

            def __init__(self, body: str):
                self.body = body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.body

        file_xml = """<?xml version="1.0" encoding="utf-8"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/dav/file.mkv</d:href>
            <d:propstat><d:prop><d:resourcetype /></d:prop></d:propstat>
          </d:response>
        </d:multistatus>
        """

        with patch("app.service.shared_protocols.webdav.urlopen", return_value=FakeResponse(file_xml)):
            result = protocol.check_rename_ready(
                "https://nas.example/dav/Movie.2024.1080p.mkv",
                "https://nas.example/dav/Movie.Safe.mkv",
                SharedPathContext(path_type="webdav", username="user", secret="password", has_secret=True),
            )

        self.assertFalse(result.success)
        self.assertEqual("WebDAV 目标文件已存在", result.message)

    def test_local_protocol_declares_atomic_rename_capability(self):
        capabilities = get_protocol("local").capabilities()

        self.assertIn(RemoteProtocolCapability.SCAN.value, capabilities.remote_capabilities)
        self.assertIn(RemoteProtocolCapability.ATOMIC_RENAME.value, capabilities.remote_capabilities)
        self.assertNotIn(RemoteProtocolCapability.COPY_DELETE_RENAME.value, capabilities.remote_capabilities)

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
