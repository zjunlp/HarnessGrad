"""Focused contract checks for the standalone submission mock."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest
import time

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("hg_mock_submissions", ROOT / "console/uat/mock_submissions.py")
MOCK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOCK)


class MockContractTests(unittest.TestCase):
    def test_scenario_rows_match_v1_required_shape(self):
        required = {"name", "dataset", "split", "side", "sampling", "rounds", "trials", "tasks", "available", "reason", "expected_minutes"}
        self.assertEqual(len(MOCK.SCENARIOS), 3)
        self.assertTrue(any(row["available"] is False for row in MOCK.SCENARIOS))
        for row in MOCK.SCENARIOS:
            self.assertTrue(required <= row.keys())

    def test_named_rejection_cases_and_invalid_score_are_separate(self):
        archive = b"test archive bytes"
        cases = {
            "reject-unsafe-path.zip": "unsafe-path",
            "reject-unsafe-entry.zip": "unsafe-entry",
            "reject-payload-too-large.zip": "payload-too-large",
            "reject-manifest-invalid.zip": "manifest-invalid",
            "reject-smoke-failed.zip": "smoke-failed",
        }
        for filename, expected in cases.items():
            status, body = MOCK.STORE.create({"scenario": "demo-valid"}, (filename, archive))
            self.assertEqual(body["error"]["code"], expected)
            self.assertIn(status, (400, 413))
        for filename in ("reject-payload-too-large.zip", "reject-archive-too-large.zip"):
            status, body = MOCK.STORE.create({"scenario": "demo-valid"}, (filename, archive))
            self.assertEqual(body["error"]["code"], filename.removeprefix("reject-").removesuffix(".zip"))
            self.assertEqual(status, 413)
        result = MOCK.result_for("demo-invalid")
        self.assertGreater(result["pools"]["dev"]["invalid"], 0)
        self.assertIsNone(result["pools"]["dev"]["score"])
        self.assertTrue(result["warnings"])

    def test_every_frozen_error_code_has_a_frontend_translation(self):
        contract = (ROOT / "docs/harness-submission-api.md").read_text(encoding="utf-8")
        section = contract.split("## 6. 错误码词表", 1)[1].split("\n---", 1)[0]
        codes = set(re.findall(r"^\| `([^`]+)` \|", section, re.MULTILINE))
        source = (ROOT / "console/static/submit.js").read_text(encoding="utf-8")
        mapping = source.split("const ERROR_MESSAGES = {", 1)[1].split("\n  };", 1)[0]
        mapped = set(re.findall(r'^\s*"([^"]+)": \[', mapping, re.MULTILINE))
        self.assertEqual(len(codes), 26)
        self.assertEqual(mapped, codes)

    def test_state_is_monotonic_and_result_counts_are_honest(self):
        status, accepted = MOCK.STORE.create({"scenario": "demo-invalid"}, ("valid.tar.gz", b"unique-state"))
        self.assertEqual(status, 202)
        ident = accepted["submission_id"]
        item = MOCK.STORE.items[ident]
        before = item["created_ts"]
        observed = []
        for offset in (0.0, MOCK.STEP_SECONDS * 0.5, MOCK.STEP_SECONDS * 1.1, MOCK.STEP_SECONDS * 2.1):
            item["created_ts"] = before - offset
            observed.append(MOCK.STORE.view(ident)["status"])
        self.assertEqual(observed, ["queued", "queued", "running", "scored"])
        result = MOCK.result_for("demo-invalid")
        for pool in result["pools"].values():
            self.assertEqual(pool["n"], pool["measured"] + pool["invalid"])
            if pool["invalid"]:
                self.assertIsNone(pool["score"])

    def test_failed_fixture_has_no_result(self):
        status, accepted = MOCK.STORE.create({"scenario": "demo-valid"}, ("demo-failed.tar.gz", b"unique-failed"))
        self.assertEqual(status, 202)
        item = MOCK.STORE.items[accepted["submission_id"]]
        item["created_ts"] -= MOCK.STEP_SECONDS * 2.1
        detail = MOCK.STORE.view(accepted["submission_id"])
        self.assertEqual(detail["status"], "failed")
        self.assertIsNone(detail["result"])
        self.assertEqual(detail["error"]["code"], "sandbox-unavailable")

    def test_filename_fixtures_cover_all_archive_rejection_codes(self):
        archive_codes = MOCK.REJECTION_CODES & {
            "bad-archive", "payload-too-large", "too-many-entries", "archive-too-large",
            "unsafe-path", "unsafe-entry", "ambiguous-root", "manifest-missing",
            "manifest-invalid", "entrypoint-missing", "backend-unsupported", "install-missing",
            "env-kind-unsupported", "smoke-failed"}
        for index, code in enumerate(sorted(archive_codes)):
            status, body = MOCK.STORE.create({"scenario": "demo-valid"}, (f"reject-{code}.tar.gz", f"bytes-{index}".encode()))
            self.assertEqual(body["error"]["code"], code)
            self.assertIn(status, (400, 413))

    def test_tree_identity_is_explicitly_mocked(self):
        status, body = MOCK.STORE.create({"scenario": "demo-valid"}, ("identity.tar.gz", b"unique-identity"))
        self.assertEqual(status, 202)
        self.assertTrue(body["tree_sha256"].startswith("mock:"))

    def test_query_boundary_rejects_unknown_duplicate_and_range(self):
        for raw in ("unknown=x", "limit=1&limit=2"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    MOCK.checked_query(raw, {"limit", "cursor"})
        self.assertEqual(MOCK.checked_query("limit=10&cursor=id", {"limit", "cursor"}),
                         {"limit": "10", "cursor": "id"})


if __name__ == "__main__":
    unittest.main()
