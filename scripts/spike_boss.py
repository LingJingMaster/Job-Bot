"""D1 spike：监听页面自己发出的 Boss 岗位详情响应，或离线统计本地 JSON。

真实浏览器采集需要人手动登录。``--from-json`` 只读取本地文件，不是真实采集，
也不能用来判断详情接口现在是否仍可用。

不连接数据库，不读取账号密码，不把 Cookie 写入数据库或日志。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSER_PROFILE_DIR = REPO_ROOT / "data" / "browser-profile"
RAW_SPIKE_DIR = REPO_ROOT / "data" / "raw" / "spike"
DETAIL_PATH = "/wapi/zpgeek/job/detail.json"
HOME_URL = "https://www.zhipin.com/"

DEFAULT_OBSERVATION_TARGET = 10
COLLECT_MAX_JOBS = 30
COLLECT_MAX_MINUTES = 20
CONSECUTIVE_PARSE_FAILURE_LIMIT = 3
LOOP_WAIT_MS = 500

OFFLINE_BANNER = "离线 JSON 字段统计：不是真实采集，不能据此判断接口是否可用。"
VERIFICATION_TEXT_MARKERS = ("安全验证", "滑动验证", "请完成验证", "拖动滑块")


@dataclass(frozen=True)
class FieldReport:
    """一条详情响应的字段完整度。不包含招聘者姓名或 encryptUserId。"""

    http_status: int | None
    business_code: object
    zpdata_keys: tuple[str, ...]
    zpdata_empty: bool
    has_job_name: bool
    has_post_description: bool
    salary_present: bool
    salary_unreadable: bool
    verification_page: bool | None
    parseable: bool
    field_failure: bool
    problems: tuple[str, ...]


def _character_is_unreadable(character: str) -> bool:
    """私有区、未分配、控制字符和替换字符视为不可读。普通中文、数字和标点保留。"""
    if ord(character) == 0xFFFD:
        return True
    return unicodedata.category(character) in {"Cc", "Cf", "Co", "Cs", "Cn"}


def salary_has_unreadable_characters(salary_desc: object) -> bool:
    if not isinstance(salary_desc, str):
        return False
    return any(_character_is_unreadable(character) for character in salary_desc)


def _nonempty_text(mapping: dict, key: str) -> bool:
    value = mapping.get(key)
    return isinstance(value, str) and value.strip() != ""


def redact_sensitive(value):
    """去掉 bossInfo.name 与任意层的 encryptUserId。encryptId 保留。"""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key == "encryptUserId":
                continue
            if key == "bossInfo" and isinstance(item, dict):
                boss_info = {}
                for boss_key, boss_value in item.items():
                    if boss_key in {"name", "encryptUserId"}:
                        continue
                    boss_info[boss_key] = redact_sensitive(boss_value)
                redacted[key] = boss_info
                continue
            redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def assert_sensitive_keys_removed(value) -> None:
    if isinstance(value, dict):
        assert "encryptUserId" not in value
        for key, item in value.items():
            if key == "bossInfo" and isinstance(item, dict):
                assert "name" not in item
            assert_sensitive_keys_removed(item)
    elif isinstance(value, list):
        for item in value:
            assert_sensitive_keys_removed(item)


def split_payload(payload: dict) -> tuple[int | None, dict]:
    """接受原始响应体，或 ``{"http_status": int, "body": {...}}`` 信封。"""
    if not isinstance(payload, dict):
        raise TypeError("JSON 顶层必须是对象")
    if "body" in payload and "http_status" in payload and isinstance(payload["body"], dict):
        return _require_http_status(payload["http_status"]), payload["body"]
    body = {key: item for key, item in payload.items() if key not in {"http_status", "_notice"}}
    status = payload.get("http_status")
    if status is None and "http_status" not in payload:
        return None, body
    return _require_http_status(status), body


def _require_http_status(status: object) -> int:
    if type(status) is not int:
        raise TypeError("http_status 必须是整数")
    return status


def assess_detail(http_status: int | None, body: dict, *, verification_page: bool | None) -> FieldReport:
    if not isinstance(body, dict):
        return assess_non_object(http_status, verification_page=verification_page)

    business_code = body.get("code")
    zp_data = body.get("zpData")
    zpdata_empty = not isinstance(zp_data, dict) or len(zp_data) == 0
    zpdata_keys = tuple(sorted(zp_data.keys())) if isinstance(zp_data, dict) else ()
    job_info = zp_data.get("jobInfo") if isinstance(zp_data, dict) else None
    if not isinstance(job_info, dict):
        job_info = {}

    has_job_name = _nonempty_text(job_info, "jobName")
    has_post_description = _nonempty_text(job_info, "postDescription")
    salary_desc = job_info.get("salaryDesc") if job_info else None
    salary_present = isinstance(salary_desc, str)
    salary_unreadable = salary_has_unreadable_characters(salary_desc)

    problems: list[str] = []
    if verification_page is True:
        problems.append("出现验证页")
    if http_status is not None and http_status != 200:
        problems.append(f"HTTP 状态非 200（{http_status}）")
    if business_code != 0:
        problems.append(f"业务 code 非成功（{business_code!r}）")
    if zpdata_empty:
        problems.append("zpData 为空")
    if isinstance(zp_data, dict) and zp_data and not has_job_name:
        problems.append("缺少 jobName")
    if isinstance(zp_data, dict) and zp_data and not has_post_description:
        problems.append("缺少 postDescription")
    if salary_present and salary_unreadable:
        problems.append("salaryDesc 含不可读字符")
    elif job_info and "salaryDesc" in job_info and not salary_present:
        problems.append("salaryDesc 不是字符串")

    transport_ok = (http_status is None or http_status == 200) and business_code == 0 and not zpdata_empty
    field_failure = transport_ok and (not has_job_name or not has_post_description)
    parseable = (
        verification_page is not True
        and (http_status is None or http_status == 200)
        and business_code == 0
        and not zpdata_empty
        and has_job_name
        and has_post_description
    )
    return FieldReport(
        http_status=http_status,
        business_code=business_code,
        zpdata_keys=zpdata_keys,
        zpdata_empty=zpdata_empty,
        has_job_name=has_job_name,
        has_post_description=has_post_description,
        salary_present=salary_present,
        salary_unreadable=salary_unreadable,
        verification_page=verification_page,
        parseable=parseable,
        field_failure=field_failure,
        problems=tuple(problems),
    )


def assess_non_object(http_status: int | None, *, verification_page: bool | None) -> FieldReport:
    problems = ["响应体不是 JSON 对象"]
    if verification_page is True:
        problems.insert(0, "出现验证页")
    if http_status is not None and http_status != 200:
        problems.append(f"HTTP 状态非 200（{http_status}）")
    return FieldReport(
        http_status=http_status,
        business_code=None,
        zpdata_keys=(),
        zpdata_empty=True,
        has_job_name=False,
        has_post_description=False,
        salary_present=False,
        salary_unreadable=False,
        verification_page=verification_page,
        parseable=False,
        field_failure=False,
        problems=tuple(problems),
    )


def format_report(report: FieldReport) -> str:
    if report.http_status is None:
        http_text = "未提供"
    else:
        http_text = str(report.http_status)
    if report.business_code is None and not report.zpdata_keys and "响应体不是 JSON 对象" in report.problems:
        code_text = "未提供"
    else:
        code_text = repr(report.business_code)
    keys_text = "、".join(report.zpdata_keys) if report.zpdata_keys else "（无）"
    if report.verification_page is True:
        verification_text = "是"
    elif report.verification_page is False:
        verification_text = "否"
    else:
        verification_text = "未观察（离线，不是真实采集）"
    if not report.salary_present:
        salary_text = "未提供"
    elif report.salary_unreadable:
        salary_text = "是"
    else:
        salary_text = "否"
    if report.parseable:
        verdict = "可解析"
    elif report.field_failure:
        verdict = "失败"
    else:
        verdict = "未计为可解析"
    lines = [
        f"HTTP 状态: {http_text}",
        f"业务 code: {code_text}",
        f"zpData 键: {keys_text}",
        f"jobName: {'有' if report.has_job_name else '无'}",
        f"postDescription: {'有' if report.has_post_description else '无'}",
        f"salaryDesc 含不可读字符: {salary_text}",
        f"验证页: {verification_text}",
        f"判定: {verdict}",
    ]
    if report.problems:
        lines.append("问题: " + "；".join(report.problems))
    return "\n".join(lines)


def decide_stop(
    *,
    count: int,
    target: int,
    consecutive_parse_failures: int,
    report: FieldReport,
    elapsed_seconds: float,
    max_seconds: float,
    user_interrupted: bool,
) -> str | None:
    """§3.3 的停止条件。命中任一条件即停止，调用方不得自动重试。"""
    if user_interrupted:
        return "user_interrupt"
    if report.verification_page is True:
        return "verification_page"
    if report.http_status not in (None, 200):
        return "http_not_ok"
    if report.business_code != 0:
        return "business_code"
    if report.zpdata_empty:
        return "empty_zpdata"
    if consecutive_parse_failures >= CONSECUTIVE_PARSE_FAILURE_LIMIT:
        return "consecutive_parse_failures"
    if count >= target:
        return "target_reached"
    if elapsed_seconds >= max_seconds:
        return "time_limit"
    return None


def counts_as_parse_failure(report: FieldReport) -> bool:
    if report.field_failure:
        return True
    return "响应体不是 JSON 对象" in report.problems and report.http_status in (None, 200)


def save_redacted_body(directory: Path, index: int, http_status: int, body: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    redacted = redact_sensitive(body)
    assert_sensitive_keys_removed(redacted)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = directory / f"s{http_status}-{stamp}-{index:03d}.json"
    path.write_text(json.dumps(redacted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def ingest_detail_response(
    *,
    http_status: int,
    body,
    verification_page: bool,
    index: int,
    raw_dir: Path,
) -> FieldReport:
    if isinstance(body, dict):
        save_redacted_body(raw_dir, index, http_status, body)
        return assess_detail(http_status, body, verification_page=verification_page)
    return assess_non_object(http_status, verification_page=verification_page)


def looks_like_verification(url: str, visible_text: str) -> bool:
    if any(marker in visible_text for marker in VERIFICATION_TEXT_MARKERS):
        return True
    path = urlsplit(url).path.lower()
    return "captcha" in path or "/verify" in path


def load_json_file(json_path: Path) -> dict:
    if not json_path.is_file():
        raise FileNotFoundError(str(json_path))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("JSON 顶层必须是对象")
    return payload


def run_from_json(json_path: Path) -> int:
    print(OFFLINE_BANNER)
    try:
        payload = load_json_file(json_path)
        http_status, body = split_payload(payload)
    except FileNotFoundError:
        print(f"找不到文件: {json_path}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"无法读取离线 JSON: {exc}", file=sys.stderr)
        return 2
    report = assess_detail(http_status, body, verification_page=None)
    print(f"--- 离线文件 {json_path.name} ---")
    print(format_report(report))
    parseable_count = 1 if report.parseable else 0
    failure_count = 1 if report.field_failure else 0
    print(f"统计: 可解析={parseable_count} 失败={failure_count}")
    return 0


def _page_visible_text(page) -> str:
    parts: list[str] = []
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if title:
        parts.append(title)
    try:
        snippet = page.inner_text("body", timeout=1000)[:4000]
    except Exception:
        snippet = ""
    if snippet:
        parts.append(snippet)
    return "\n".join(parts)


def context_has_verification(context) -> bool:
    for page in list(context.pages):
        url = ""
        try:
            url = page.url or ""
        except Exception:
            url = ""
        if looks_like_verification(url, _page_visible_text(page)):
            return True
    return False


def print_live_summary(reports: list[FieldReport], stop_reason: str) -> None:
    parseable_count = sum(1 for report in reports if report.parseable)
    failure_count = sum(1 for report in reports if report.field_failure)
    unreadable_count = sum(1 for report in reports if report.salary_unreadable)
    saw_verification = stop_reason == "verification_page" or any(
        report.verification_page is True for report in reports
    )
    print("==== 汇总 ====")
    print(f"停止原因: {stop_reason}")
    print(f"观察条数: {len(reports)}")
    print(f"可解析: {parseable_count}")
    print(f"失败: {failure_count}")
    print(f"salaryDesc 含不可读字符的条数: {unreadable_count}")
    print(f"验证页: {'是' if saw_verification else '否'}")
    print("已停止，不自动重试。")


def run_live(limit: int, max_minutes: int) -> int:
    if limit < DEFAULT_OBSERVATION_TARGET or limit > COLLECT_MAX_JOBS:
        print(f"--limit 须在 {DEFAULT_OBSERVATION_TARGET} 到 {COLLECT_MAX_JOBS} 之间", file=sys.stderr)
        return 2
    if max_minutes < 1 or max_minutes > COLLECT_MAX_MINUTES:
        print(f"--max-minutes 须在 1 到 {COLLECT_MAX_MINUTES} 之间", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 Playwright，无法打开浏览器。离线验收请使用 --from-json。", file=sys.stderr)
        return 1

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    print("D1 spike：只监听页面自己发出的岗位详情 GET。")
    print("请在打开的浏览器中手动登录，并自行点开岗位。脚本不等待账号密码。")
    print("遇到验证页会停止，不绕过验证。按 Ctrl+C 可提前结束。")
    print(f"目标条数: {limit}。最长 {max_minutes} 分钟。")
    print(f"响应体目录: {RAW_SPIKE_DIR}")

    state: dict = {
        "stop_reason": None,
        "consecutive_parse_failures": 0,
        "records": [],
        "verification_seen": False,
    }
    started = time.monotonic()
    max_seconds = max_minutes * 60

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            accept_downloads=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response) -> None:
                if state["stop_reason"] is not None:
                    return
                request = response.request
                if request.method != "GET" or DETAIL_PATH not in response.url:
                    return
                safe_path = urlsplit(response.url).path
                http_status = response.status
                try:
                    raw_text = response.text()
                except Exception:
                    print(f"接口路径: {safe_path}")
                    print("读取响应体失败")
                    body = None
                else:
                    try:
                        body = json.loads(raw_text)
                    except json.JSONDecodeError:
                        body = None
                index = len(state["records"]) + 1
                report = ingest_detail_response(
                    http_status=http_status,
                    body=body,
                    verification_page=bool(state["verification_seen"]),
                    index=index,
                    raw_dir=RAW_SPIKE_DIR,
                )
                state["records"].append(report)
                if counts_as_parse_failure(report):
                    state["consecutive_parse_failures"] += 1
                elif report.parseable:
                    state["consecutive_parse_failures"] = 0
                print(f"--- 第 {index} 条 ---")
                print(f"接口路径: {safe_path}")
                print(format_report(report))
                reason = decide_stop(
                    count=index,
                    target=limit,
                    consecutive_parse_failures=state["consecutive_parse_failures"],
                    report=report,
                    elapsed_seconds=time.monotonic() - started,
                    max_seconds=max_seconds,
                    user_interrupted=False,
                )
                if reason is not None:
                    state["stop_reason"] = reason

            context.on("response", on_response)
            try:
                page.goto(HOME_URL, wait_until="domcontentloaded")
            except Exception:
                print("打开首页失败。若浏览器已关闭，本次结束。", file=sys.stderr)
                state["stop_reason"] = state["stop_reason"] or "user_interrupt"

            while state["stop_reason"] is None:
                if not context.pages:
                    state["stop_reason"] = "user_interrupt"
                    break
                if time.monotonic() - started >= max_seconds:
                    state["stop_reason"] = "time_limit"
                    break
                try:
                    state["verification_seen"] = context_has_verification(context)
                except Exception:
                    state["verification_seen"] = False
                if state["verification_seen"]:
                    state["stop_reason"] = "verification_page"
                    break
                try:
                    page.wait_for_timeout(LOOP_WAIT_MS)
                except KeyboardInterrupt:
                    state["stop_reason"] = "user_interrupt"
                    break
                except Exception:
                    if not context.pages:
                        state["stop_reason"] = "user_interrupt"
                        break
                    raise
        except KeyboardInterrupt:
            state["stop_reason"] = "user_interrupt"
        finally:
            context.close()

    stop_reason = state["stop_reason"] or "user_interrupt"
    print_live_summary(state["records"], stop_reason)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boss 详情接口 D1 spike（监听或离线统计）")
    parser.add_argument(
        "--from-json",
        type=Path,
        help="对一个本地 JSON 文件做字段完整度统计。这是离线验收，不是真实采集。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_OBSERVATION_TARGET,
        help=f"真实监听时的目标条数，范围 {DEFAULT_OBSERVATION_TARGET}–{COLLECT_MAX_JOBS}，默认 {DEFAULT_OBSERVATION_TARGET}",
    )
    parser.add_argument(
        "--max-minutes",
        type=int,
        default=COLLECT_MAX_MINUTES,
        help=f"真实监听最长分钟数，最大 {COLLECT_MAX_MINUTES}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.from_json is not None:
        return run_from_json(args.from_json)
    return run_live(limit=args.limit, max_minutes=args.max_minutes)


if __name__ == "__main__":
    raise SystemExit(main())
