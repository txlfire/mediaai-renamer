"""锁文件与发布入口一致，不能仅固定直接依赖或在打包时丢失锁。"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DependencyLockTest(unittest.TestCase):
    def test_all_direct_versions_and_runtime_dependencies_are_hashed(self):
        lock = (ROOT / "backend/requirements.lock").read_text(encoding="utf-8")
        entries = {}
        for block in re.split(r"(?m)(?=^[a-zA-Z0-9_-]+==)", lock):
            if not re.match(r"[a-zA-Z0-9_-]+==", block): continue
            requirement = re.fullmatch(r"([A-Za-z0-9_-]+)==([A-Za-z0-9.+-]+)(?:\s*;.*)?", block.splitlines()[0].rstrip(" \\"))
            self.assertIsNotNone(requirement)
            self.assertRegex(block, r"--hash=sha256:[a-f0-9]{64}")
            entries[requirement[1].lower().replace("_", "-")] = requirement[2]
        for line in (ROOT / "backend/requirements.txt").read_text().splitlines():
            if not line.strip() or line.startswith("#"): continue
            requirement = re.fullmatch(r"([A-Za-z0-9_-]+)(?:\[[A-Za-z0-9_,-]+\])?==([A-Za-z0-9.+-]+)", line)
            self.assertIsNotNone(requirement)
            self.assertEqual(requirement[2], entries[requirement[1].lower()])
        self.assertTrue({"starlette", "pydantic", "pydantic-core", "anyio", "h11", "httpcore", "uvloop", "colorama"} <= entries.keys())
        self.assertNotIn(".codex/", lock)
        self.assertNotIn("--index-url", lock)

    def test_container_ci_and_both_packages_use_the_lock(self):
        for path in ("docker/Dockerfile", ".github/workflows/docker-ghcr.yml", ".github/workflows/webdav-integration.yml"):
            with self.subTest(path=path):
                content = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn("--require-hashes", content)
                self.assertIn("requirements.lock", content)
        for path in ("scripts/package-release.ps1", "scripts/package-release.sh"):
            content = (ROOT / path).read_text(encoding="utf-8")
            self.assertGreaterEqual(content.count("requirements.lock"), 2)
            self.assertIn("Version must be a semantic version without path characters.", content)
