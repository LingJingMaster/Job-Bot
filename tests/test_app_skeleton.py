from pathlib import Path

import pytest

from jobbot.config import ENV_KEYS

ROOT = Path(__file__).resolve().parents[1]


def _visible(app: object) -> str:
    chunks: list[str] = []
    for element_name in ("title", "header", "subheader", "markdown", "warning", "error", "info", "caption", "text"):
        for element in getattr(app, element_name):
            chunks.append(str(element.value))
    return "\n".join(chunks)


def _prepare_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> None:
    values = {key: "" for key in ENV_KEYS}
    values.update(
        {
            "DATA_DIR": str(tmp_path),
            "LLM_MODE": "live",
            "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5",
            "COLLECT_MAX_JOBS": "30",
            "COLLECT_MAX_MINUTES": "20",
            "TASK_BUDGET_CALLS": "200",
            "TASK_BUDGET_USD": "2.0",
            "LOG_LEVEL": "INFO",
        }
    )
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_settings_page_shows_matrix_mask_and_does_not_claim_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from streamlit.testing.v1 import AppTest

    secret = "local-" + ("q" * 16)
    _prepare_env(monkeypatch, tmp_path, LLM_API_KEY=secret)
    app = AppTest.from_file(str(ROOT / "app" / "main.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "能力矩阵" in text or "自动抽取" in text
    assert "自动抽取" in text and "不可用" in text
    assert "手动录入事实" in text and "可用" in text
    assert "B0" in text and "可用" in text
    assert "规则校验" in text
    assert "Boss 自动采集" in text and "不可用" in text
    assert "不是录用概率" in text
    assert str(tmp_path) in text
    assert "200" in text
    assert secret not in text
    assert secret[-4:] in text
    assert "真实 Boss 采集尚未跑过" in text
    assert "不会假装已连通" in text
    app.button[1].click().run()
    clicked = _visible(app)
    assert "没有发出网络请求" in clicked
    assert "不能把未连接说成已连通" in clicked


def test_tracking_page_does_not_contact_platforms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "tracking.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "不向任何外部地址发出请求" in text
    assert "没有把任何岗位记成已投递" in text


def test_labels_page_does_not_score_without_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "labels.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "本次没有打分" in text
    assert "分数" not in text
    assert "排名" not in text


def test_match_page_does_not_rank_without_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "match.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "本次没有排序" in text
    assert "不是录用概率" in text


def test_materials_page_does_not_pretend_approved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "materials.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "未批准" in text
    assert "没有生成材料" in text


def test_resume_page_does_not_auto_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    app = AppTest.from_file(str(ROOT / "app" / "views" / "resume.py"))
    app.run()
    assert not app.exception, app.exception
    text = _visible(app)
    assert "不会自动确认" in text


def test_invalid_config_is_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path, LLM_MODE="turbo")
    app = AppTest.from_file(str(ROOT / "app" / "main.py"))
    app.run()
    text = _visible(app)
    assert "LLM_MODE" in text
    assert "未保存" in text
