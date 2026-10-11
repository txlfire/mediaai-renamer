"""外部请求预算只用临时数据库和模拟transport，不调用真实服务。"""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import json
import io
import multiprocessing
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError

from app.core.config import AppSettings, ExternalRequestSettings, ExternalSiteLimits, externalRequestSettingsFromDict
from app.core.database import ensure_database, CURRENT_SCHEMA_VERSION
from app.service.external_request_budget_service import (
    ExternalRequestBudget, ExternalBudgetWaitError, ExternalBudgetUnavailable, _siteKey, transportDeadline,
)


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): return b"{}"


def acquireInChild(databasePath, origin, output, finish, abandon=False, timeout=2, rate=100):
    """独立进程只访问父测试临时库；不初始化正式配置或发起网络请求。"""
    path = Path(databasePath)
    settings = AppSettings(data_dir=path.parent, database_path=path,
        external_requests=ExternalRequestSettings(4, ExternalSiteLimits(rate, 1), ExternalSiteLimits(rate, 1)))
    budget = ExternalRequestBudget(settings, "ai")
    try:
        token = budget.acquire(_siteKey(origin), time.monotonic() + timeout)
    except ExternalBudgetWaitError:
        output.put((None, time.time()))
        return
    output.put((token, time.time()))
    finish.wait(8)
    if abandon:
        os._exit(0)
    budget.release(token)


class ExternalRequestBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "request.db",
            external_requests=ExternalRequestSettings(4, ExternalSiteLimits(100, 1), ExternalSiteLimits(100, 1)))
        ensure_database(self.settings)

    def sql(self, sql, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(sql, args).fetchall()
            connection.commit()
            return rows

    def child(self, origin, *, abandon=False, timeout=2, rate=100):
        context = multiprocessing.get_context("spawn")
        output, finish = context.Queue(), context.Event()
        process = context.Process(target=acquireInChild, args=(str(self.settings.database_path), origin, output,
            finish, abandon, timeout, rate))
        process.start()
        def cleanup():
            finish.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            output.close()
            output.join_thread()
        self.addCleanup(cleanup)
        return process, output, finish

    def test_site_key_ignores_credentials_paths_and_default_port_aliases(self):
        self.assertEqual(_siteKey("https://API.example/v1?q=secret"), _siteKey("https://api.example:443/v2"))
        self.assertNotEqual(_siteKey("http://api.example"), _siteKey("https://api.example"))
        with self.assertRaisesRegex(ValueError, "地址无效"):
            _siteKey("https://user:secret@api.example")

    def test_same_origin_serializes_across_distinct_service_clients_and_wait_expires_without_send(self):
        first, second = ExternalRequestBudget(self.settings, "ai"), ExternalRequestBudget(self.settings, "tmdb")
        request = Request("http://localhost:9999/v1")
        with patch("app.service.external_request_budget_service.urlopen", return_value=Response()) as opener:
            with first.open(request, 1):
                with self.assertRaisesRegex(ExternalBudgetWaitError, "未发送"):
                    with second.open(request, 0.07): pass
                self.assertEqual(1, len(self.sql("SELECT token FROM external_request_leases")))
            with second.open(request, 1): pass
        self.assertEqual(2, opener.call_count)
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))
        dump = str(self.sql("SELECT * FROM external_request_sites"))
        self.assertNotIn("localhost", dump)

    def test_global_concurrency_is_not_four_per_client(self):
        budget = ExternalRequestBudget(self.settings, "tmdb")
        tokens = [budget.acquire(_siteKey(f"https://site-{i}.example"), time.monotonic() + 1) for i in range(4)]
        try:
            with self.assertRaises(ExternalBudgetWaitError):
                ExternalRequestBudget(self.settings, "ai").acquire(_siteKey("https://fifth.example"), time.monotonic() + 0.07)
        finally:
            for token in tokens: budget.release(token)

    def test_two_threads_cannot_admit_same_site_twice(self):
        key = _siteKey("https://api.example")
        def acquire(_):
            try: return ExternalRequestBudget(self.settings, "ai").acquire(key, time.monotonic() + 0.1)
            except ExternalBudgetWaitError: return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            tokens = list(executor.map(acquire, range(2)))
        self.assertEqual(1, sum(token is not None for token in tokens))
        for token in tokens:
            if token: ExternalRequestBudget(self.settings, "ai").release(token)

    def test_response_read_remains_inflight_until_actual_read_exits(self):
        reading, finish = Event(), Event()
        class SlowResponse(Response):
            def read(self, *_):
                reading.set(); finish.wait(2); return b"{}"
        budget = ExternalRequestBudget(self.settings, "ai")
        request = Request("https://api.example")
        def call():
            with budget.open(request, 0.1) as response: response.read()
        with patch("app.service.external_request_budget_service.urlopen", return_value=SlowResponse()), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call)
            self.assertTrue(reading.wait(1)); time.sleep(0.12)
            try:
                self.assertEqual(1, len(self.sql("SELECT token FROM external_request_leases")))
                with self.assertRaises(ExternalBudgetWaitError):
                    with budget.open(request, 0.03): pass
            finally: finish.set()
            with self.assertRaises(TimeoutError): future.result()
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_rate_wait_uses_total_budget_and_reduces_transport_timeout(self):
        settings = replace(self.settings, external_requests=ExternalRequestSettings())
        budget = ExternalRequestBudget(settings, "ai")
        request = Request("https://api.example")
        with patch("app.service.external_request_budget_service.urlopen", return_value=Response()) as opener:
            with budget.open(request, 1): pass
            with self.assertRaises(ExternalBudgetWaitError):
                with budget.open(request, 0.05): pass
            with transportDeadline(time.monotonic() + 0.6):
                with budget.open(request, 5): pass
        self.assertEqual(2, opener.call_count)
        self.assertLess(opener.call_args.kwargs["timeout"], 0.5)

    def test_dead_process_lease_is_reclaimed_but_live_unknown_pid_is_retained(self):
        budget = ExternalRequestBudget(self.settings, "ai")
        self.sql("INSERT INTO external_request_leases VALUES ('old',?,12345,1,1,0)", (_siteKey("https://old.example"),))
        with patch("app.service.external_request_budget_service.isProcessAlive", return_value=True):
            with self.assertRaises(ExternalBudgetWaitError): budget.acquire(_siteKey("https://new.example"), time.monotonic() + 0.03)
        with patch("app.service.external_request_budget_service.isProcessAlive", side_effect=ValueError("unknown")):
            with self.assertRaises(ExternalBudgetWaitError): budget.acquire(_siteKey("https://new.example"), time.monotonic() + 0.03)
        with patch("app.service.external_request_budget_service.isProcessAlive", return_value=False):
            token = budget.acquire(_siteKey("https://new.example"), time.monotonic() + 1)
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases WHERE token='old'"))
        budget.release(token)

    def test_missing_database_fails_closed_without_initialization(self):
        settings = replace(self.settings, database_path=self.settings.data_dir / "missing.db")
        with self.assertRaisesRegex(ExternalBudgetUnavailable, "未发送"):
            ExternalRequestBudget(settings, "ai").acquire(_siteKey("https://api.example"), time.monotonic() + 1)
        self.assertFalse(settings.database_path.exists())

    def test_group_overrides_and_strict_configuration(self):
        config = externalRequestSettingsFromDict({"ai": {"max_concurrency": 2}, "metadata": {"requests_per_second": 5},
            "providers": {"tmdb": {"max_concurrency": 3}}})
        self.assertEqual(2, config.forService("ai").max_concurrency)
        self.assertEqual((5, 3), (config.forService("tmdb").requests_per_second, config.forService("tmdb").max_concurrency))
        for bad in ({"max_concurrency": True}, {"max_concurrency": 0}, {"ai": {"requests_per_second": float("nan")}},
                    {"ai": {"requests_per_second": 10**500}},
                    {"ai": {"max_concurrency": 1.5}}, {"providers": {"unknown": {}}}, {"ai": "bad"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): externalRequestSettingsFromDict(bad)

    def test_v32_upgrade_creates_empty_budget_tables_idempotently(self):
        self.sql("DROP TABLE external_request_leases"); self.sql("DROP TABLE external_request_sites")
        self.sql("UPDATE app_meta SET value='32' WHERE key='schema_version'")
        ensure_database(self.settings); ensure_database(self.settings)
        self.assertEqual([], self.sql("SELECT * FROM external_request_sites"))
        self.assertEqual([], self.sql("SELECT * FROM external_request_leases"))
        self.assertEqual(str(CURRENT_SCHEMA_VERSION), self.sql("SELECT value FROM app_meta WHERE key='schema_version'")[0][0])

    def test_cross_process_same_origin_cannot_admit_second_request(self):
        first, output, finish = self.child("https://api.example")
        token, _ = output.get(timeout=6)
        self.assertIsNotNone(token)
        second, result, secondFinish = self.child("https://api.example/v2", timeout=0.15)
        self.assertIsNone(result.get(timeout=6)[0])
        second.join(5)
        self.assertEqual(0, second.exitcode)
        finish.set()
        first.join(5)
        self.assertEqual(0, first.exitcode)
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_cross_process_rate_and_confirmed_exit_recovery(self):
        process, output, finish = self.child("https://api.example", abandon=True, rate=2)
        token, _ = output.get(timeout=6)
        self.assertIsNotNone(token)
        # 把起点设为当前时间，避免Windows spawn启动耗时掩盖速率等待。
        self.sql("UPDATE external_request_sites SET last_started_at=?", (time.time(),))
        finish.set()
        process.join(5)
        self.assertEqual(0, process.exitcode)
        settings = replace(self.settings, external_requests=ExternalRequestSettings())
        budget = ExternalRequestBudget(settings, "tmdb")
        started = time.monotonic()
        replacement = budget.acquire(_siteKey("https://api.example"), started + 2)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases WHERE token=?", (token,)))
        budget.release(replacement)

    def test_cross_process_global_limit_is_shared(self):
        budget = ExternalRequestBudget(self.settings, "tmdb")
        tokens = [budget.acquire(_siteKey(f"https://parent-{i}.example"), time.monotonic() + 1) for i in range(3)]
        try:
            process, output, finish = self.child("https://fourth.example")
            self.assertIsNotNone(output.get(timeout=6)[0])
            with self.assertRaises(ExternalBudgetWaitError):
                budget.acquire(_siteKey("https://fifth.example"), time.monotonic() + 0.07)
            finish.set()
            process.join(5)
            self.assertEqual(0, process.exitcode)
        finally:
            for token in tokens: budget.release(token)

    def test_busy_database_consumes_wait_budget_without_send(self):
        with closing(sqlite3.connect(self.settings.database_path)) as locked:
            locked.execute("BEGIN IMMEDIATE")
            with patch("app.service.external_request_budget_service.urlopen") as opener:
                started = time.monotonic()
                with self.assertRaises(ExternalBudgetWaitError):
                    with ExternalRequestBudget(self.settings, "ai").open(Request("https://api.example"), 0.08): pass
                self.assertLess(time.monotonic() - started, 0.5)
                opener.assert_not_called()
            locked.rollback()

    def test_corrupt_state_fails_closed_with_safe_message(self):
        key = _siteKey("https://api.example")
        self.sql("INSERT INTO external_request_sites VALUES (?,0,'private-bad-value',0)", (key,))
        with patch("app.service.external_request_budget_service.urlopen") as opener:
            with self.assertRaisesRegex(ExternalBudgetUnavailable, "状态无效") as error:
                with ExternalRequestBudget(self.settings, "ai").open(Request("https://api.example"), 1): pass
            self.assertNotIn("private", str(error.exception))
            opener.assert_not_called()

    def test_response_close_failure_retains_actual_slot(self):
        class UnclosedResponse(Response):
            def __exit__(self, *_): raise OSError("synthetic-close-failure")
        with patch("app.service.external_request_budget_service.urlopen", return_value=UnclosedResponse()):
            with self.assertRaises(OSError):
                with ExternalRequestBudget(self.settings, "ai").open(Request("https://api.example"), 1): pass
        self.assertEqual(1, len(self.sql("SELECT token FROM external_request_leases")))

    def test_release_failure_does_not_overwrite_known_response(self):
        budget = ExternalRequestBudget(self.settings, "ai")
        with patch("app.service.external_request_budget_service.urlopen", return_value=Response()):
            with budget.open(Request("https://api.example"), 1) as response:
                self.assertEqual(b"{}", response.read())
                with closing(sqlite3.connect(self.settings.database_path)) as locked:
                    locked.execute("BEGIN IMMEDIATE")
                    budget.release(self.sql("SELECT token FROM external_request_leases")[0][0])
                    self.assertEqual(1, len(self.sql("SELECT token FROM external_request_leases")))
                    locked.rollback()
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_real_local_http_redirect_does_not_forward_authorization(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{server.server_port}/source", headers={"Authorization": "Bearer synthetic"})
            with self.assertRaises(HTTPError) as error:
                with ExternalRequestBudget(self.settings, "ai").open(request, 1): pass
            self.assertEqual(302, error.exception.code)
            self.assertEqual([("/source", "Bearer synthetic")], seen)
            self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_site_capacity_is_bounded_and_inactive_history_is_cleaned(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO external_request_sites VALUES (?,0,.01,?)",
                ((f"history-{i}", time.time()) for i in range(1024)))
            connection.commit()
        budget = ExternalRequestBudget(self.settings, "ai")
        with self.assertRaises(ExternalBudgetUnavailable):
            budget.acquire(_siteKey("https://new.example"), time.monotonic() + 1)
        self.sql("UPDATE external_request_sites SET updated_at=0")
        token = budget.acquire(_siteKey("https://new.example"), time.monotonic() + 1)
        self.assertEqual(925, self.sql("SELECT COUNT(*) FROM external_request_sites")[0][0])
        budget.release(token)

    def test_ai_connection_retry_uses_actual_http_budget_for_each_attempt(self):
        from app.service.ai_provider import AiProviderConfig, DeepSeekProvider
        provider = DeepSeekProvider(AiProviderConfig(provider="deepseek", model="deepseek-chat", api_key="synthetic",
            base_url="https://api.example", timeout_ms=1500, max_retries=1,
            requestBudget=ExternalRequestBudget(self.settings, "ai")))
        class JsonResponse(Response):
            def read(self, *_): return b'{"choices":[{"message":{"content":"ok"}}]}'
        with patch("app.service.external_request_budget_service.urlopen", side_effect=[
                HTTPError("synthetic", 429, "limited", {"Retry-After": "0"}, None), JsonResponse()]) as opener, \
                patch("app.service.ai_provider.url_request.urlopen") as legacy:
            result = provider.test_connection()
        self.assertEqual("success", result["status"])
        self.assertEqual(2, opener.call_count)
        legacy.assert_not_called()
        self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_ai_connection_wait_failure_explicitly_reports_not_sent(self):
        from app.service.ai_provider import AiProviderConfig, DeepSeekProvider
        budget = ExternalRequestBudget(self.settings, "ai")
        token = budget.acquire(_siteKey("https://api.example"), time.monotonic() + 1)
        provider = DeepSeekProvider(AiProviderConfig(provider="deepseek", model="deepseek-chat", api_key="synthetic",
            base_url="https://api.example", timeout_ms=100, max_retries=3, requestBudget=budget))
        try:
            with patch("app.service.external_request_budget_service.urlopen") as opener:
                result = provider.test_connection()
                opener.assert_not_called()
            self.assertEqual("budget_wait", result["error_type"])
            self.assertIn("未发送", result["message"])
            self.assertEqual(1, result["attempts"])
        finally: budget.release(token)

    def test_metadata_login_search_and_details_each_acquire_actual_slot(self):
        from app.schema.media import ParsedMediaName
        from app.service.tmdb_client import TmdbClient
        from app.service.tvdb_client import TvdbClient
        parsed = ParsedMediaName(title="Example", media_type="episode", year=None, season=1, episode=1)
        class JsonResponse(Response):
            def __init__(self, payload): self.body = json.dumps(payload).encode()
            def read(self, *_): return self.body
        cases = [
            (TmdbClient(api_key="synthetic", requestBudget=ExternalRequestBudget(self.settings, "tmdb")), [
                {"results": [{"id": 1, "name": "Example"}]}, {"id": 1, "name": "Example"}]),
            (TvdbClient(api_key="synthetic", requestBudget=ExternalRequestBudget(self.settings, "tvdb")), [
                {"data": {"token": "synthetic"}}, {"data": [{"tvdb_id": "1", "name": "Example"}]},
                {"data": {"name": "Example"}}, {"data": {"episodes": [{"seasonNumber": 1, "number": 1, "name": "Pilot"}]}}]),
        ]
        for client, payloads in cases:
            with self.subTest(provider=type(client).__name__):
                def opened(request, timeout):
                    self.assertEqual(1, len(self.sql("SELECT token FROM external_request_leases")))
                    return JsonResponse(payloads.pop(0))
                expected = len(payloads)
                with patch("app.service.external_request_budget_service.urlopen", side_effect=opened) as opener:
                    self.assertEqual(1, len(client.search(parsed)))
                self.assertEqual(expected, opener.call_count)
                self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_expired_parent_deadline_does_not_allow_metadata_login_or_search(self):
        from app.service.tvdb_client import TvdbClient
        client = TvdbClient(api_key="synthetic", max_retries=3, requestBudget=ExternalRequestBudget(self.settings, "tvdb"))
        with transportDeadline(time.monotonic() - 1), patch("app.service.external_request_budget_service.urlopen") as opener:
            with self.assertRaises(ExternalBudgetWaitError): client.test_connection()
            opener.assert_not_called()

    def test_bangumi_and_proxy_connection_tests_use_shared_transport(self):
        from app.service.bangumi_client import BangumiClient
        from app.service.douban_proxy_client import DoubanProxyClient
        clients = [BangumiClient(requestBudget=ExternalRequestBudget(self.settings, "bangumi")),
            DoubanProxyClient("http://127.0.0.1:9999", requestBudget=ExternalRequestBudget(self.settings, "douban_proxy"))]
        for client in clients:
            with self.subTest(provider=client.provider), patch("app.service.external_request_budget_service.urlopen", return_value=Response()) as opener:
                self.assertTrue(client.test_connection())
                opener.assert_called_once()
                self.assertEqual([], self.sql("SELECT token FROM external_request_leases"))

    def test_existing_imdb_reachability_test_is_budgeted_but_does_not_add_search(self):
        from app.service.settings_service import test_imdb_connection
        with patch("app.service.external_request_budget_service.urlopen", return_value=Response()) as opener:
            self.assertEqual("success", test_imdb_connection(self.settings)["status"])
            opener.assert_called_once()
        budget = ExternalRequestBudget(self.settings, "imdb")
        token = budget.acquire(_siteKey("https://www.imdb.com/"), time.monotonic() + 1)
        try:
            with patch("app.service.settings_service.get_effective_settings", return_value={"imdb.timeout_ms": 50}), \
                    patch("app.service.external_request_budget_service.urlopen") as opener:
                result = test_imdb_connection(self.settings)
                opener.assert_not_called()
            self.assertEqual("failed", result["status"])
            self.assertIn("未发送", result["message"])
        finally: budget.release(token)

    def test_tmdb_authentication_fallback_does_not_reset_deadline(self):
        from app.schema.media import ParsedMediaName
        from app.service.metadata_provider_registry import TmdbMetadataProvider
        from app.service.metadata_service import match_metadata_candidates
        parsed = ParsedMediaName("movie", "Example", None, None, None)
        effective = {"tmdb.enabled": True, "tmdb.v4_token": "synthetic-token", "tmdb.api_key": "synthetic-key", "tmdb.timeout_ms": 1000}
        class EmptySearch(Response):
            def read(self, *_): return b'{"results":[]}'
        for module, call in [("metadata_provider_registry", lambda: TmdbMetadataProvider(self.settings, 1).search(parsed)),
                             ("metadata_service", lambda: match_metadata_candidates(self.settings, parsed))]:
            with self.subTest(module=module):
                calls = []
                def opened(request, timeout):
                    calls.append(timeout)
                    if len(calls) == 1:
                        time.sleep(0.08)
                        raise HTTPError("synthetic", 401, "auth", {}, None)
                    return EmptySearch()
                with patch(f"app.service.{module}.get_effective_settings", return_value=effective), \
                        patch("app.service.external_request_budget_service.urlopen", side_effect=opened):
                    call()
                self.assertEqual(2, len(calls))
                self.assertLess(calls[1], 0.95)

    def test_isolated_start_passes_budget_configuration_to_stdin(self):
        from app.service.isolated_task_service import startTaskProcess
        from dataclasses import asdict
        from types import SimpleNamespace
        class Pipe(io.BytesIO):
            def close(self): self.saved = self.getvalue(); super().close()
        process = SimpleNamespace(pid=os.getpid(), stdin=Pipe())
        with patch("app.service.isolated_task_service.subprocess.Popen", return_value=process), \
                patch("app.service.isolated_task_service.resolveRuntimeLogging", return_value=self.settings.logging):
            startTaskProcess(self.settings, "app.scan_worker", {"job_id": 1}, lambda pid: None)
        self.assertEqual(asdict(self.settings.external_requests), json.loads(process.stdin.saved)["external_requests"])

    def test_all_isolated_worker_entries_restore_custom_budget(self):
        from app import scan_worker, rename_worker, rollback_worker
        from types import SimpleNamespace
        custom = {"max_concurrency": 2, "ai": {"requests_per_second": 3}, "providers": {"tmdb": {"max_concurrency": 2}}}
        payload = {"database_path": str(self.settings.database_path), "data_dir": str(self.settings.data_dir),
            "log_dir": str(self.settings.data_dir), "log_level": "INFO", "log_max_size_mb": 10,
            "log_backup_count": 2, "external_requests": custom, "batch_size": 10, "batch_interval_seconds": 0,
            "job_id": 1, "operation_id": 1, "plan_id": 1, "token": "synthetic"}
        for module, function in [(scan_worker, "executeAcceptedScan"), (rename_worker, "executeAcceptedRename"),
                                 (rollback_worker, "executeAcceptedRollback")]:
            with self.subTest(module=module.__name__), patch.object(module, "setup_logging"), \
                    patch.object(module, "shutdown_logging"), patch.object(module, function) as execute, \
                    patch.object(module.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))):
                self.assertEqual(0, module.main())
                self.assertEqual(externalRequestSettingsFromDict(custom), execute.call_args.args[0].external_requests)
