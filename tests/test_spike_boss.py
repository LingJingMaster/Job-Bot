"""离线验收 scripts/spike_boss.py。不打开浏览器，不依赖 pytest 或 jobbot。"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import unicodedata
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE_PATH = REPO_ROOT / "scripts" / "spike_boss.py"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "boss_detail"


def load_spike_module():
    spec = importlib.util.spec_from_file_location("spike_boss_under_test", SPIKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {SPIKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SpikeBossOfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_spike_module()

    def assess_fixture(self, filename: str):
        payload = json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))
        http_status, body = self.module.split_payload(payload)
        return self.module.assess_detail(http_status, body, verification_page=None)

    def run_from_json(self, filename: str) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = self.module.main(["--from-json", str(FIXTURE_DIR / filename)])
        self.assertEqual(exit_code, 0)
        return buffer.getvalue()

    def test_fixtures_are_marked_synthetic(self):
        readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("合成，不是真实抓取", readme)
        json_paths = list(FIXTURE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(json_paths), 5)
        for path in json_paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn("合成，不是真实抓取", text)

    def test_complete_sample_is_parseable(self):
        report = self.assess_fixture("complete.json")
        stdout = self.run_from_json("complete.json")
        self.assertTrue(report.parseable)
        self.assertFalse(report.field_failure)
        self.assertFalse(report.salary_unreadable)
        self.assertEqual(report.http_status, 200)
        self.assertEqual(report.business_code, 0)
        self.assertIn("可解析=1", stdout)
        self.assertIn("失败=0", stdout)
        self.assertIn("不是真实采集", stdout)

    def test_missing_job_name_is_failure(self):
        report = self.assess_fixture("missing_job_name.json")
        stdout = self.run_from_json("missing_job_name.json")
        self.assertFalse(report.has_job_name)
        self.assertTrue(report.has_post_description)
        self.assertTrue(report.field_failure)
        self.assertFalse(report.parseable)
        self.assertIn("失败=1", stdout)
        self.assertIn("可解析=0", stdout)
        self.assertIn("缺少 jobName", stdout)

    def test_missing_post_description_is_failure(self):
        report = self.assess_fixture("missing_post_description.json")
        stdout = self.run_from_json("missing_post_description.json")
        self.assertTrue(report.has_job_name)
        self.assertFalse(report.has_post_description)
        self.assertTrue(report.field_failure)
        self.assertFalse(report.parseable)
        self.assertIn("失败=1", stdout)
        self.assertIn("可解析=0", stdout)
        self.assertIn("缺少 postDescription", stdout)

    def test_unreadable_salary_is_reported(self):
        payload = json.loads((FIXTURE_DIR / "unreadable_salary.json").read_text(encoding="utf-8"))
        _http_status, body = self.module.split_payload(payload)
        salary_desc = body["zpData"]["jobInfo"]["salaryDesc"]
        self.assertTrue(any(unicodedata.category(character) == "Co" for character in salary_desc))

        report = self.assess_fixture("unreadable_salary.json")
        stdout = self.run_from_json("unreadable_salary.json")
        self.assertTrue(report.parseable)
        self.assertFalse(report.field_failure)
        self.assertTrue(report.salary_unreadable)
        self.assertIn("salaryDesc 含不可读字符", stdout)
        self.assertIn("salaryDesc 含不可读字符: 是", stdout)

    def test_http_and_business_code_failure_is_not_parseable(self):
        report = self.assess_fixture("business_failure.json")
        stdout = self.run_from_json("business_failure.json")
        self.assertEqual(report.http_status, 503)
        self.assertEqual(report.business_code, 900001)
        self.assertFalse(report.parseable)
        self.assertFalse(report.field_failure)
        self.assertIn("未计为可解析", stdout)
        self.assertIn("HTTP 状态非 200", stdout)
        self.assertIn("业务 code 非成功", stdout)
        reason = self.module.decide_stop(
            count=1,
            target=10,
            consecutive_parse_failures=0,
            report=report,
            elapsed_seconds=1,
            max_seconds=20 * 60,
            user_interrupted=False,
        )
        self.assertEqual(reason, "http_not_ok")

    def test_saved_body_drops_name_and_encrypt_user_id(self):
        payload = json.loads((FIXTURE_DIR / "complete.json").read_text(encoding="utf-8"))
        http_status, body = self.module.split_payload(payload)
        with tempfile.TemporaryDirectory() as directory:
            report = self.module.ingest_detail_response(
                http_status=http_status,
                body=body,
                verification_page=False,
                index=1,
                raw_dir=Path(directory),
            )
            saved_paths = list(Path(directory).glob("*.json"))
            self.assertEqual(len(saved_paths), 1)
            saved_text = saved_paths[0].read_text(encoding="utf-8")
        self.assertTrue(report.parseable)
        self.assertNotIn("encryptUserId", saved_text)
        self.assertNotIn("synthetic-user-900001", saved_text)
        self.assertNotIn("合成姓名甲", saved_text)
        self.assertIn("合成岗位-假数据-001", saved_text)
        self.assertIn("synthetic-job-900001", saved_text)
        saved = json.loads(saved_text)
        self.assertNotIn("name", saved["zpData"]["bossInfo"])
        self.assertEqual(saved["zpData"]["bossInfo"]["title"], "合成称谓")

    def test_source_has_no_forbidden_actions(self):
        source = SPIKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("btn-startchat", source)
        self.assertNotRegex(source, r"\.click\s*\(")
        self.assertNotRegex(source, r"\.fill\s*\(")
        self.assertIsNone(
            re.search(r"(?i)\b(send|upload)\b[\s\S]{0,120}\.(click|fill)\s*\(", source)
        )
        self.assertIsNone(
            re.search(r"(?i)\.(click|fill)\s*\([\s\S]{0,120}\b(send|upload)\b", source)
        )
        self.assertIn("launch_persistent_context", source)
        self.assertIn("headless=False", source)
        self.assertIn(self.module.DETAIL_PATH, source)
        self.assertEqual(
            self.module.BROWSER_PROFILE_DIR,
            self.module.REPO_ROOT / "data" / "browser-profile",
        )
        self.assertEqual(self.module.RAW_SPIKE_DIR, self.module.REPO_ROOT / "data" / "raw" / "spike")
        self.assertGreaterEqual(self.module.DEFAULT_OBSERVATION_TARGET, 10)

    def test_from_json_does_not_launch_browser(self):
        original = self.module.run_live

        def fail_if_called(*args, **kwargs):
            raise AssertionError("离线子命令不应打开浏览器")

        self.module.run_live = fail_if_called
        try:
            stdout = self.run_from_json("complete.json")
        finally:
            self.module.run_live = original
        self.assertIn(self.module.OFFLINE_BANNER, stdout)

    def test_stop_on_target_and_consecutive_field_failures(self):
        complete = self.assess_fixture("complete.json")
        self.assertEqual(
            self.module.decide_stop(
                count=10,
                target=10,
                consecutive_parse_failures=0,
                report=complete,
                elapsed_seconds=5,
                max_seconds=20 * 60,
                user_interrupted=False,
            ),
            "target_reached",
        )
        missing = self.assess_fixture("missing_job_name.json")
        self.assertEqual(
            self.module.decide_stop(
                count=3,
                target=10,
                consecutive_parse_failures=3,
                report=missing,
                elapsed_seconds=5,
                max_seconds=20 * 60,
                user_interrupted=False,
            ),
            "consecutive_parse_failures",
        )

    def test_verification_marker_does_not_treat_login_wording_as_block(self):
        self.assertTrue(self.module.looks_like_verification("https://example.test/verify", ""))
        self.assertTrue(self.module.looks_like_verification("https://example.test/jobs", "请完成验证"))
        self.assertFalse(
            self.module.looks_like_verification("https://www.zhipin.com/", "验证码登录")
        )


if __name__ == "__main__":
    unittest.main()
