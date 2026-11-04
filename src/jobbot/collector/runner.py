"""模式 A：监听页面自己发出的岗位详情。模式 B 不点选、不填写。

Playwright 只在 _listen 里导入。导入本模块不会打开浏览器。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jobbot.config import load_config
from jobbot.contracts import CollectLimits, CollectorBlocked, JobBotError, ParseError
from jobbot.db.repository import get_by_id

from jobbot.collector.database import open_database
from jobbot.collector.detail_parser import parse_detail
from jobbot.collector.raw_files import write_raw_response
from jobbot.collector.runs import pending_user_stop, read_params, start_run, write_run_progress
from jobbot.collector.stop_conditions import (
    REASON_AUTO_UNAVAILABLE,
    REASON_BROWSER,
    REASON_USER,
    REASON_VERIFICATION,
    business_code_is_ok,
    page_requests_stop,
    stop_after_parse_failure,
    stop_after_store,
    zpdata_is_empty,
)
from jobbot.collector.store import save_draft

DETAIL_PATH = "/wapi/zpgeek/job/detail.json"
HOME_URL = "https://www.zhipin.com/"
LOOP_WAIT_MS = 500


@dataclass(frozen=True)
class DetailOutcome:
    stop_reason: str | None
    stored: bool
    parse_failed: bool
    n_seen: int
    n_ok: int
    n_fail: int
    raw_path: str | None
    attempt_again: bool = False


def process_detail(
    connection,
    run_id: str,
    *,
    http_status: int | None,
    body: dict[str, Any] | None,
    verification_page: bool,
    user_stop: bool,
    elapsed_seconds: float,
    raw_directory: Path,
) -> DetailOutcome:
    """处理一条详情响应。命中停止条件后写入 stop_reason，不会再处理下一次。"""
    run = get_by_id(connection, "collect_run", run_id)
    if run is None:
        raise JobBotError(
            f"没有编号为 {run_id} 的采集。这条响应没有入库。",
            saved=False,
            next_step="先创建采集运行。",
        )
    if run["stop_reason"]:
        return _outcome(run, stored=False, parse_failed=False, raw_path=None)

    params = read_params(run["params"])
    max_jobs = int(params.get("max_jobs") or 30)
    max_seconds = int(params.get("max_minutes") or 20) * 60
    consecutive = int(params.get("consecutive_parse_failures") or 0)
    n_seen = int(run["n_seen"])
    n_ok = int(run["n_ok"])
    n_fail = int(run["n_fail"])
    user_requested = user_stop or pending_user_stop(connection, run_id)

    if user_requested or verification_page or elapsed_seconds >= max_seconds or n_seen >= max_jobs:
        reason = REASON_USER if user_requested else REASON_VERIFICATION if verification_page else None
        if reason is None and elapsed_seconds >= max_seconds:
            reason = "max_minutes"
        if reason is None:
            reason = "max_jobs"
        write_run_progress(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail,
            consecutive_parse_failures=consecutive,
            stop_reason=reason,
        )
        return _outcome(get_by_id(connection, "collect_run", run_id), stored=False, parse_failed=False, raw_path=None)

    n_seen += 1
    raw_path = write_raw_response(raw_directory, n_seen, http_status, body)

    if http_status != 200:
        return _reject(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail + 1,
            consecutive=consecutive,
            reason="http_not_200",
            raw_path=raw_path,
            parse_failed=False,
        )

    if not isinstance(body, dict):
        consecutive += 1
        reason = stop_after_parse_failure(consecutive)
        return _reject(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail + 1,
            consecutive=consecutive,
            reason=reason,
            raw_path=raw_path,
            parse_failed=True,
        )

    code = body.get("code") if "code" in body else None
    if "code" not in body or not business_code_is_ok(code):
        return _reject(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail + 1,
            consecutive=consecutive,
            reason="business_code_not_zero",
            raw_path=raw_path,
            parse_failed=False,
        )

    if zpdata_is_empty(body.get("zpData") if "zpData" in body else None):
        return _reject(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail + 1,
            consecutive=consecutive,
            reason="zpdata_empty",
            raw_path=raw_path,
            parse_failed=False,
        )

    try:
        draft = parse_detail(body)
    except ParseError:
        consecutive += 1
        reason = stop_after_parse_failure(consecutive)
        return _reject(
            connection,
            run_id,
            n_seen=n_seen,
            n_ok=n_ok,
            n_fail=n_fail + 1,
            consecutive=consecutive,
            reason=reason,
            raw_path=raw_path,
            parse_failed=True,
        )

    draft = draft.model_copy(update={"raw_path": str(raw_path)})
    _job, _snapshot, created = save_draft(connection, draft, run_id=run_id)
    if created:
        n_ok += 1
    reason = stop_after_store(
        seen_count=n_seen,
        max_jobs=max_jobs,
        elapsed_seconds=elapsed_seconds,
        max_seconds=max_seconds,
    )
    write_run_progress(
        connection,
        run_id,
        n_seen=n_seen,
        n_ok=n_ok,
        n_fail=n_fail,
        consecutive_parse_failures=0,
        stop_reason=reason,
    )
    return DetailOutcome(
        stop_reason=reason,
        stored=created,
        parse_failed=False,
        n_seen=n_seen,
        n_ok=n_ok,
        n_fail=n_fail,
        raw_path=str(raw_path),
        attempt_again=False,
    )


def run_assist(run_id: str | None = None, *, connection=None) -> str:
    with open_database(connection) as connection:
        if run_id is None:
            settings = load_config()
            run_id = start_run(
                "assist",
                None,
                CollectLimits(
                    max_jobs=settings.collect_max_jobs,
                    max_minutes=settings.collect_max_minutes,
                ),
                connection=connection,
            )
        run = get_by_id(connection, "collect_run", run_id)
        if run is None:
            raise JobBotError(
                f"没有编号为 {run_id} 的采集。浏览器没有打开。",
                saved=False,
                next_step="从采集页重新启动。",
            )
        if run["status"] != "running":
            return run_id
        if run["mode"] == "auto":
            write_run_progress(
                connection,
                run_id,
                n_seen=int(run["n_seen"]),
                n_ok=int(run["n_ok"]),
                n_fail=int(run["n_fail"]),
                consecutive_parse_failures=0,
                stop_reason=REASON_AUTO_UNAVAILABLE,
            )
            return run_id
        try:
            _listen(connection, run_id)
        except CollectorBlocked as exc:
            current = get_by_id(connection, "collect_run", run_id)
            if current is not None and current["status"] == "running":
                params = read_params(current["params"])
                write_run_progress(
                    connection,
                    run_id,
                    n_seen=int(current["n_seen"]),
                    n_ok=int(current["n_ok"]),
                    n_fail=int(current["n_fail"]),
                    consecutive_parse_failures=int(params.get("consecutive_parse_failures") or 0),
                    stop_reason=exc.stop_reason,
                    status="failed",
                )
            raise
        return run_id


def _listen(connection, run_id: str) -> None:
    sync_playwright = _load_playwright()
    settings = load_config()
    profile_dir = settings.data_dir / "browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = settings.data_dir / "raw" / "collect" / run_id
    started = time.monotonic()
    state: dict[str, str | None] = {"stop": None}

    def on_response(response) -> None:
        if state["stop"] is not None:
            return
        request = response.request
        path = urlsplit(response.url or "").path
        if request.method != "GET" or DETAIL_PATH not in path:
            return
        try:
            payload = json.loads(response.text())
        except Exception:
            payload = None
        body = payload if isinstance(payload, dict) else None
        outcome = process_detail(
            connection,
            run_id,
            http_status=response.status,
            body=body,
            verification_page=False,
            user_stop=pending_user_stop(connection, run_id),
            elapsed_seconds=time.monotonic() - started,
            raw_directory=raw_dir,
        )
        if outcome.stop_reason:
            state["stop"] = outcome.stop_reason

    print("浏览器即将打开。请自己登录，并自己点开岗位。程序只监听详情接口。")
    print("不会读取账号密码，也不会把登录态写入数据库或日志。")
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                accept_downloads=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                context.on("response", on_response)
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded")
                except Exception as exc:
                    state["stop"] = state["stop"] or REASON_BROWSER
                    raise CollectorBlocked(
                        f"浏览器没有打开职位页：{exc}",
                        stop_reason=REASON_BROWSER,
                        saved=True,
                        next_step="确认网络可用后重新启动。不会自动再请求。",
                    ) from exc
                while state["stop"] is None:
                    if not context.pages:
                        _stop_from_loop(connection, run_id, REASON_USER)
                        state["stop"] = REASON_USER
                        break
                    if pending_user_stop(connection, run_id):
                        _stop_from_loop(connection, run_id, REASON_USER)
                        state["stop"] = REASON_USER
                        break
                    current = get_by_id(connection, "collect_run", run_id)
                    params = read_params(current["params"]) if current else {}
                    elapsed = time.monotonic() - started
                    if elapsed >= int(params.get("max_minutes") or 20) * 60:
                        _stop_from_loop(connection, run_id, "max_minutes")
                        state["stop"] = "max_minutes"
                        break
                    seen = int(current["n_seen"]) if current else 0
                    if _context_requests_stop(context, seen):
                        _stop_from_loop(connection, run_id, REASON_VERIFICATION)
                        state["stop"] = REASON_VERIFICATION
                        break
                    try:
                        page.wait_for_timeout(LOOP_WAIT_MS)
                    except KeyboardInterrupt:
                        _stop_from_loop(connection, run_id, REASON_USER)
                        state["stop"] = REASON_USER
                        break
            except KeyboardInterrupt:
                if state["stop"] is None:
                    _stop_from_loop(connection, run_id, REASON_USER)
                    state["stop"] = REASON_USER
            finally:
                context.close()
    except CollectorBlocked:
        raise
    except Exception as exc:
        raise CollectorBlocked(
            f"浏览器没有保持打开：{exc}",
            stop_reason=REASON_BROWSER,
            saved=True,
            next_step="查看已保存条数后，可以重新启动。不会自动再请求。",
        ) from exc
    current = get_by_id(connection, "collect_run", run_id)
    reason = state["stop"] or (current["stop_reason"] if current else None) or REASON_USER
    if current is not None and current["status"] == "running":
        _stop_from_loop(connection, run_id, reason)
    fresh = get_by_id(connection, "collect_run", run_id)
    if fresh is not None:
        print(
            f"停止原因: {fresh['stop_reason']}。看到 {fresh['n_seen']} 条，"
            f"写入 {fresh['n_ok']} 条，失败 {fresh['n_fail']} 条。"
        )
    print("已停止，不会自动再请求。")


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CollectorBlocked(
            "没有安装 Playwright，浏览器没有打开。采集未开始。",
            stop_reason="playwright_missing",
            saved=False,
            next_step="安装 Playwright 与浏览器后再启动。本次没有修改依赖清单。",
        ) from exc
    return sync_playwright


def _context_requests_stop(context, jobs_already_seen: int) -> bool:
    for page in list(context.pages):
        try:
            url = page.url or ""
        except Exception:
            url = ""
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        try:
            visible = page.inner_text("body", timeout=1000)[:4000]
        except Exception:
            visible = ""
        if page_requests_stop(url, f"{title}\n{visible}", jobs_already_seen=jobs_already_seen):
            return True
    return False


def _stop_from_loop(connection, run_id: str, reason: str) -> None:
    current = get_by_id(connection, "collect_run", run_id)
    if current is None or current["status"] != "running":
        return
    params = read_params(current["params"])
    write_run_progress(
        connection,
        run_id,
        n_seen=int(current["n_seen"]),
        n_ok=int(current["n_ok"]),
        n_fail=int(current["n_fail"]),
        consecutive_parse_failures=int(params.get("consecutive_parse_failures") or 0),
        stop_reason=reason,
    )


def _reject(
    connection,
    run_id: str,
    *,
    n_seen: int,
    n_ok: int,
    n_fail: int,
    consecutive: int,
    reason: str | None,
    raw_path: Path,
    parse_failed: bool,
) -> DetailOutcome:
    write_run_progress(
        connection,
        run_id,
        n_seen=n_seen,
        n_ok=n_ok,
        n_fail=n_fail,
        consecutive_parse_failures=consecutive,
        stop_reason=reason,
    )
    return DetailOutcome(
        stop_reason=reason,
        stored=False,
        parse_failed=parse_failed,
        n_seen=n_seen,
        n_ok=n_ok,
        n_fail=n_fail,
        raw_path=str(raw_path),
        attempt_again=False,
    )


def _outcome(run: dict | None, *, stored: bool, parse_failed: bool, raw_path: str | None) -> DetailOutcome:
    if run is None:
        raise JobBotError("采集记录丢失。", saved=False, next_step="重新启动采集。")
    return DetailOutcome(
        stop_reason=run["stop_reason"],
        stored=stored,
        parse_failed=parse_failed,
        n_seen=int(run["n_seen"]),
        n_ok=int(run["n_ok"]),
        n_fail=int(run["n_fail"]),
        raw_path=raw_path,
        attempt_again=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模式 A 采集：只监听岗位详情")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    try:
        run_assist(args.run_id)
    except CollectorBlocked as exc:
        print(exc.user_text(), file=sys.stderr)
        return 1
    except JobBotError as exc:
        print(exc.user_text(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
