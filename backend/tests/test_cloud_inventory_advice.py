"""离线建议误判保护；全部输入为合成本地观察，不取正文或访问云盘。"""

import unittest

import test_cloud_inventory as stateTests
import test_cloud_inventory_api as apiTests
from app.service import cloud_inventory_advice_service as advice
from app.service.cloud_inventory_service import CloudInventoryError

ROOT = stateTests.ROOT


class CloudInventoryAdviceTest(unittest.TestCase):
    def setUp(self):
        self.state = stateTests.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings = self.state.settings

    def observed(self, names, *, partial=False, directories=False):
        jobId = self.state.job()
        client = stateTests.SyntheticClient({ROOT: [stateTests.entry(name, directory=directories) for name in names]},
            fail="unavailable" if partial else None)
        if partial:
            with self.assertRaises(CloudInventoryError):
                self.state.runJob(jobId, client)
        else:
            self.state.runJob(jobId, client)
        return jobId

    def analyze(self, jobId, **options):
        return advice.buildCloudInventoryAdvice(self.settings, jobId, self.state.actor.id, **options)

    def test_extensions_size_and_ambiguous_ad_words_never_classify_as_ads(self):
        jobId = self.observed(["notes.txt", "photo.jpg", "short.mp4", "AD.2024.mkv", "广告狂人.S01E01.mkv", "sample.mkv"])
        page = self.analyze(jobId)
        self.assertTrue(all(item["decision"] == "keep" for item in page["items"]))
        self.assertEqual(0, page["contentReadBytes"])
        self.assertFalse(page["networkAccess"])
        self.assertFalse(page["externalSubmission"])

    def test_sidecars_artwork_documents_and_structural_directories_win_over_evidence(self):
        jobId = self.observed(["更多资源.srt", "更多资源.nfo", "poster-www.example.com.jpg",
            "fanart-更多资源.png", "README-www.example.com.txt", "使用说明-更多资源.txt", "更多资源.sha256",
            "trailer-更多资源.mp4", "花絮-更多资源.mkv"])
        page = self.analyze(jobId)
        self.assertTrue(all(item["protection"] and item["decision"] == "keep" for item in page["items"]))
        directoryJob = self.observed(["BDMV", "CERTIFICATE", ".actors", "Season 01", "S02", "第一季", "extras"], directories=True)
        self.assertTrue(all(item["protection"] for item in self.analyze(directoryJob)["items"]))

    def test_promotional_evidence_only_requests_manual_review_and_never_selects(self):
        jobId = self.observed(["更多资源-www.example.com.txt", "更多资源.mp4", "www.example.com.png"])
        page = self.analyze(jobId)
        self.assertEqual(["medium", "low", "low"], [item["evidenceLevel"] for item in page["items"]])
        self.assertTrue(all(item["decision"] == "review" for item in page["items"]))
        self.assertTrue(all(not item["selected"] and not item["actionAvailable"] for item in page["items"]))
        self.assertFalse(page["writeAvailable"])

    def test_keep_exception_has_priority_but_is_not_silently_persisted(self):
        jobId = self.observed(["更多资源-www.example.com.txt"])
        first = self.analyze(jobId)
        entryId = first["items"][0]["id"]
        kept = self.analyze(jobId, keepEntryIds=[entryId])
        self.assertEqual("user_keep", kept["items"][0]["protection"])
        self.assertEqual("keep", kept["items"][0]["decision"])
        self.assertFalse(kept["keepExceptionsPersisted"])
        self.assertEqual("review", self.analyze(jobId)["items"][0]["decision"])
        self.assertNotEqual(first["rulesHash"], kept["rulesHash"])

    def test_folder_suggestions_require_explicit_media_folder_and_literal_terms(self):
        jobId = self.observed(["[论坛推广] 示例电影 (2024) 1080p", "欧美"], directories=True)
        first = self.analyze(jobId, folderTerms=["论坛推广"])
        self.assertTrue(all(item["folderSuggestion"]["status"] == "unselected" for item in first["items"]))
        folderId = first["items"][0]["id"]
        result = self.analyze(jobId, folderEntryIds=[folderId], folderTerms=["论坛推广"])
        suggestion = result["items"][0]["folderSuggestion"]
        self.assertEqual("suggested", suggestion["status"])
        self.assertEqual("示例电影 (2024) 1080p", suggestion["proposedName"])
        self.assertEqual("unknown", suggestion["conflict"])
        self.assertEqual("unselected", result["items"][1]["folderSuggestion"]["status"])

    def test_year_season_version_and_empty_name_are_protected(self):
        names = ["示例电影 (2024)", "示例 S01", "示例 收藏版", "论坛推广"]
        terms = ["2024", "S01", "收藏版", "论坛推广"]
        jobId = self.observed(names, directories=True)
        ids = [item["id"] for item in self.analyze(jobId)["items"]]
        page = self.analyze(jobId, folderEntryIds=ids, folderTerms=terms)
        self.assertTrue(all(item["folderSuggestion"]["status"] == "blocked" for item in page["items"]))
        self.assertEqual("empty_proposed_name", page["items"][-1]["folderSuggestion"]["reason"])

    def test_known_target_conflict_and_partial_observation_block_folder_advice(self):
        jobId = self.observed(["[论坛推广] 示例", "示例"], directories=True)
        folderId = self.analyze(jobId)["items"][0]["id"]
        suggestion = self.analyze(jobId, folderEntryIds=[folderId], folderTerms=["论坛推广"])["items"][0]["folderSuggestion"]
        self.assertEqual("observed_target_exists", suggestion["reason"])
        partial = self.observed(["[论坛推广] 示例"], directories=True, partial=True)
        first = self.analyze(partial)
        result = self.analyze(partial, folderEntryIds=[first["items"][0]["id"]], folderTerms=["论坛推广"])
        self.assertTrue(result["items"][0]["partialObservation"])
        self.assertEqual("partial_observation", result["items"][0]["folderSuggestion"]["reason"])

    def test_invalid_limits_foreign_ids_and_regex_terms_cannot_expand_scope(self):
        jobId = self.observed(["a.txt"])
        for options in ({"folderTerms": {}}, {"folderEntryIds": [True]}, {"folderTerms": ["../escape"]},
                        {"folderTerms": ["x\ntext"]}, {"folderTerms": ["x"]}, {"keepEntryIds": [999]},
                        {"folderEntryIds": [999]}, {"folderTerms": ["test"] * 51}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.analyze(jobId, **options)
        with self.assertRaises(CloudInventoryError):
            advice.buildCloudInventoryAdvice(self.settings, jobId, self.state.actor.id + 1)
        directoryJob = self.observed(["[.*] 示例 (2024)"], directories=True)
        first = self.analyze(directoryJob)
        result = self.analyze(directoryJob, folderEntryIds=[first["items"][0]["id"]], folderTerms=[".*"])
        self.assertEqual("示例 (2024)", result["items"][0]["folderSuggestion"]["proposedName"])

    def test_analysis_uses_bounded_local_page_and_does_not_write_audit_or_jobs(self):
        jobId = self.observed([f"{i}.txt" for i in range(205)])
        before = self.state.sql("SELECT COUNT(*) FROM audit_events")
        first = self.analyze(jobId, limit=200)
        second = self.analyze(jobId, afterId=first["nextId"], limit=200)
        self.assertEqual((200, 5), (len(first["items"]), len(second["items"])))
        self.assertEqual(before, self.state.sql("SELECT COUNT(*) FROM audit_events"))
        self.assertEqual(1, len(self.state.sql("SELECT id FROM cloud_drive_inventory_jobs")))


class CloudInventoryAdviceApiTest(unittest.TestCase):
    def setUp(self):
        self.fixture = apiTests.CloudInventoryApiTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_api_is_local_strict_and_owner_guarded(self):
        client, headers, url = self.fixture.client, self.fixture.headers, self.fixture.url + "/advice"
        result = client.post(url, headers=headers, json={})
        self.assertEqual(200, result.status_code)
        self.assertFalse(result.json()["writeAvailable"])
        for payload in ({"limit": True}, {"folderEntryIds": [True]}, {"folderTerms": ["ab"] * 51},
                        {"apiToken": "synthetic-secret"}):
            self.assertEqual(422, client.post(url, headers=headers, json=payload).status_code)
        self.assertEqual(401, client.post(url, json={}).status_code)
        self.fixture.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        self.assertEqual(404, client.post(url, headers=headers, json={}).status_code)
