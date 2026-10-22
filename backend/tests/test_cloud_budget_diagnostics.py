"""预算存储失败仅记录固定诊断，不记录SQL、路径或异常正文。"""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings
from app.core.logger import get_logger
from app.service import cloud_drive_budget_service as budget


class CloudBudgetDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = AppSettings(database_path=Path(self.temp.name) / 'not-created.sqlite3')

    def testTerminalSqliteErrorHasSafeStageAndNumericCode(self):
        error = sqlite3.OperationalError('synthetic-secret SQL /private/location')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(budget.sqlite3, 'connect', side_effect=error):
            with self.assertLogs(get_logger(budget.__name__), level='WARNING') as captured:
                with self.assertRaises(budget.CloudBudgetUnavailable) as caught:
                    with budget._connection(self.settings, write=True):
                        self.fail('存储失败时不应继续')
        self.assertEqual(caught.exception.code, 'storage_unavailable')
        self.assertEqual(len(captured.output), 1)
        self.assertIn('stage=connect', captured.output[0])
        self.assertIn('write=True', captured.output[0])
        self.assertIn('sqlite_code=5', captured.output[0])
        self.assertNotIn('synthetic-secret', captured.output[0])
        self.assertNotIn('/private/location', captured.output[0])
        self.assertFalse(self.settings.database_path.exists())

    def testExpectedBusyWaitDoesNotFloodLogs(self):
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | (1 << 8)):
            error = sqlite3.OperationalError('synthetic-secret')
            error.sqlite_errorcode = code
            with self.subTest(code=code), patch.object(budget.sqlite3, 'connect', side_effect=error):
                with self.assertNoLogs(get_logger(budget.__name__), level='WARNING'):
                    with self.assertRaises(budget._BudgetBusy):
                        with budget._connection(self.settings, allowBusy=True):
                            self.fail('应继续由有限预算控制等待')

    def testBodyAndOsFailuresAreTraceableWithoutExceptionText(self):
        self.settings.database_path.touch()
        for error in (sqlite3.OperationalError('synthetic-secret'), OSError('synthetic-secret')):
            with self.subTest(error=type(error).__name__):
                with self.assertLogs(get_logger(budget.__name__), level='WARNING') as captured:
                    with self.assertRaises(budget.CloudBudgetUnavailable):
                        with budget._connection(self.settings):
                            raise error
                self.assertIn('stage=body', captured.output[0])
                self.assertNotIn('synthetic-secret', captured.output[0])


if __name__ == '__main__':
    unittest.main()
