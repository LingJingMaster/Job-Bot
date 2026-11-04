"""采集停止条件。纯函数，不访问网络，也不会再次发起请求。"""

from __future__ import annotations

PARSE_FAILURE_LIMIT = 3

REASON_VERIFICATION = "verification_page"
REASON_HTTP = "http_not_200"
REASON_CODE = "business_code_not_zero"
REASON_ZPDATA = "zpdata_empty"
REASON_PARSE = "consecutive_parse_failures"
REASON_MAX_JOBS = "max_jobs"
REASON_MAX_MINUTES = "max_minutes"
REASON_USER = "user_stop"
REASON_PLAYWRIGHT_MISSING = "playwright_missing"
REASON_AUTO_UNAVAILABLE = "auto_mode_not_implemented"
REASON_BROWSER = "browser_launch_failed"

REASON_TEXT = {
    REASON_VERIFICATION: "出现验证页或采集过程中的登录页",
    REASON_HTTP: "详情接口 HTTP 状态不是 200",
    REASON_CODE: "详情接口业务 code 不是 0",
    REASON_ZPDATA: "详情接口 zpData 为空",
    REASON_PARSE: "连续 3 次解析失败",
    REASON_MAX_JOBS: "达到条数上限",
    REASON_MAX_MINUTES: "达到时间上限",
    REASON_USER: "用户停止",
    REASON_PLAYWRIGHT_MISSING: "没有安装浏览器驱动库",
    REASON_AUTO_UNAVAILABLE: "自动点选模式未实现",
    REASON_BROWSER: "浏览器没有打开",
}


def business_code_is_ok(code: object) -> bool:
    return type(code) is int and code == 0


def zpdata_is_empty(zp_data: object) -> bool:
    return not isinstance(zp_data, dict) or len(zp_data) == 0


def stop_before_store(
    *,
    user_stop: bool,
    verification_page: bool,
    elapsed_seconds: float,
    max_seconds: float,
    seen_before: int,
    max_jobs: int,
    http_status: int | None,
    body_is_object: bool,
    business_code: object,
    zp_empty: bool,
) -> str | None:
    """这条响应不能入库时返回停止原因。未命中则返回 None。"""
    if user_stop:
        return REASON_USER
    if verification_page:
        return REASON_VERIFICATION
    if elapsed_seconds >= max_seconds:
        return REASON_MAX_MINUTES
    if seen_before >= max_jobs:
        return REASON_MAX_JOBS
    if http_status != 200:
        return REASON_HTTP
    if not body_is_object:
        return None
    if not business_code_is_ok(business_code):
        return REASON_CODE
    if zp_empty:
        return REASON_ZPDATA
    return None


def stop_after_parse_failure(consecutive_parse_failures: int) -> str | None:
    if consecutive_parse_failures >= PARSE_FAILURE_LIMIT:
        return REASON_PARSE
    return None


def stop_after_store(*, seen_count: int, max_jobs: int, elapsed_seconds: float, max_seconds: float) -> str | None:
    if seen_count >= max_jobs:
        return REASON_MAX_JOBS
    if elapsed_seconds >= max_seconds:
        return REASON_MAX_MINUTES
    return None


def page_requests_stop(url: str, visible_text: str, *, jobs_already_seen: int) -> bool:
    """验证页始终停止。普通登录入口在还没采到岗位时留给用户自己登录。"""
    text = visible_text or ""
    if any(marker in text for marker in ("安全验证", "滑动验证", "请完成验证", "拖动滑块")):
        return True
    lowered = (url or "").lower()
    if "captcha" in lowered or "/verify" in lowered:
        return True
    if jobs_already_seen > 0 and "login" in lowered:
        return True
    return False
