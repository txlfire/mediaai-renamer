"""隔离验证凭据迁移、密钥丢失及备份恢复。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.core.config import AppSettings
from app.core.database import ensure_database
from app.service.media_source_secret import (
    _encrypt_v2_secret, decrypt_secret, encrypt_secret,
)
from app.service.settings_service import get_effective_settings, update_setting_values


class CredentialSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3")
        self.envPatch = patch.dict("os.environ", {})
        self.envPatch.start()
        self.addCleanup(self.envPatch.stop)
        import os
        os.environ.pop("MEDIAAI_CREDENTIAL_KEY", None)
        ensure_database(self.settings)

    @property
    def keyPath(self):
        return self.settings.data_dir / "secrets" / "credentials.key"

    def test_new_ciphertext_uses_independent_random_key(self):
        encrypted = encrypt_secret(self.settings, "synthetic-secret")
        self.assertTrue(encrypted.startswith("v3:"))
        self.assertTrue(self.keyPath.is_file())
        self.assertEqual("synthetic-secret", decrypt_secret(self.settings, encrypted))
        self.assertNotEqual(encrypted, encrypt_secret(self.settings, "synthetic-secret"))

    def test_environment_key_precedes_file_and_invalid_key_is_explicit(self):
        with patch.dict("os.environ", {"MEDIAAI_CREDENTIAL_KEY": Fernet.generate_key().decode("ascii")}):
            encrypted = encrypt_secret(self.settings, "synthetic-secret")
            self.assertFalse(self.keyPath.exists())
            self.assertEqual("synthetic-secret", decrypt_secret(self.settings, encrypted))
        with patch.dict("os.environ", {"MEDIAAI_CREDENTIAL_KEY": "synthetic-invalid"}):
            with self.assertRaisesRegex(ValueError, "格式无效"):
                decrypt_secret(self.settings, encrypted)

    def test_backup_restores_at_different_database_path_with_original_key(self):
        update_setting_values(self.settings, {"ai.api_key": "synthetic-secret"})
        restoredRoot = self.settings.data_dir / "restored"
        restoredRoot.mkdir()
        restored = replace(self.settings, data_dir=restoredRoot, database_path=restoredRoot / "new.sqlite3", credential_key_file=restoredRoot / "original.key")
        shutil.copy2(self.settings.database_path, restored.database_path)
        shutil.copy2(self.keyPath, restored.credential_key_file)
        ensure_database(restored)
        self.assertEqual("synthetic-secret", get_effective_settings(restored)["ai.api_key"])

    def test_missing_key_never_generates_replacement_or_clears_ciphertext(self):
        update_setting_values(self.settings, {"ai.api_key": "synthetic-secret"})
        self.keyPath.unlink()
        with self.assertRaisesRegex(ValueError, "密钥缺失"):
            ensure_database(self.settings)
        with self.assertRaisesRegex(ValueError, "密钥缺失"):
            encrypt_secret(self.settings, "another-secret")
        self.assertFalse(self.keyPath.exists())
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertTrue(connection.execute("SELECT value FROM system_settings WHERE key='ai.api_key'").fetchone()[0].startswith("v3:"))

    def test_wrong_key_is_explicit_and_does_not_fall_back_to_empty_setting(self):
        update_setting_values(self.settings, {"ai.api_key": "synthetic-secret"})
        self.keyPath.write_bytes(Fernet.generate_key())
        with self.assertRaisesRegex(ValueError, "解密失败"):
            get_effective_settings(self.settings)
        with self.assertRaises(ValueError):
            ensure_database(self.settings)

    def test_tampered_ciphertext_is_rejected(self):
        encrypted = encrypt_secret(self.settings, "synthetic-secret")
        with self.assertRaisesRegex(ValueError, "解密失败"):
            decrypt_secret(self.settings, encrypted[:-5] + "AAAAA")

    def test_sensitive_settings_and_profile_list_are_encrypted_at_rest(self):
        update_setting_values(self.settings, {
            "tmdb.api_key": "synthetic-tmdb-key",
            "ai.api_key": "synthetic-ai-key",
            "ai.provider_profiles": [{"id": "test", "name": "Test", "provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com", "api_key": "synthetic-profile-key"}],
        })
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute("SELECT value FROM system_settings WHERE key IN ('tmdb.api_key','ai.api_key','ai.provider_profiles')").fetchall()
        self.assertEqual(3, len(rows))
        for (value,) in rows:
            self.assertTrue(value.startswith("v3:"))
            self.assertNotIn("synthetic", value)
        effective = get_effective_settings(self.settings)
        self.assertEqual("synthetic-tmdb-key", effective["tmdb.api_key"])
        self.assertEqual("synthetic-profile-key", effective["ai.provider_profiles"][0]["api_key"])

    def test_v2_and_plaintext_settings_migrate_in_one_transaction(self):
        old = _encrypt_v2_secret(self.settings, "synthetic-old")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE metadata_provider_configs SET api_key_encrypted=? WHERE provider='tmdb'", (old,))
            connection.execute("INSERT INTO system_settings(key,category,value,description,sensitive,operator,created_at,updated_at) VALUES ('ai.api_key','ai','synthetic-plain','test',1,'test','now','now')")
            connection.execute("UPDATE app_meta SET value='20' WHERE key='schema_version'")
            connection.commit()
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            migrated = connection.execute("SELECT api_key_encrypted FROM metadata_provider_configs WHERE provider='tmdb'").fetchone()[0]
        self.assertTrue(migrated.startswith("v3:"))
        self.assertEqual("synthetic-old", decrypt_secret(self.settings, migrated))
        self.assertEqual("synthetic-plain", get_effective_settings(self.settings)["ai.api_key"])

    def test_failed_migration_preserves_original_rows_and_schema_version(self):
        old = _encrypt_v2_secret(self.settings, "synthetic-old")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE metadata_provider_configs SET api_key_encrypted=? WHERE provider='tmdb'", (old,))
            connection.execute("UPDATE metadata_provider_configs SET api_key_encrypted='v2:invalid' WHERE provider='tvdb'")
            connection.execute("UPDATE app_meta SET value='20' WHERE key='schema_version'")
            connection.commit()
        with self.assertRaises(ValueError):
            ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(old, connection.execute("SELECT api_key_encrypted FROM metadata_provider_configs WHERE provider='tmdb'").fetchone()[0])
            self.assertEqual("20", connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0])

    def test_concurrent_key_creation_uses_one_complete_key(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            encrypted = list(pool.map(lambda _: encrypt_secret(self.settings, "synthetic-secret"), range(8)))
        for item in encrypted:
            self.assertEqual("synthetic-secret", decrypt_secret(self.settings, item))
