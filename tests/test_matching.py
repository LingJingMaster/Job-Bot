"""匹配排序的合成数据测试。不访问网络，不下载向量模型。"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import httpx
import pytest

from jobbot.config import ENV_KEYS
from jobbot.contracts import (
    EXPLANATION_SCORE_NOTE,
    FactStructured,
    HardConstraint,
    JDParse,
    JobBotError,
    JobSnapshotDraft,
    LLMResult,
    Preference,
    ProfileSnapshot,
    Requirement,
    RequirementNormalized,
    SourceRef,
    TfidfConfig,
)
from jobbot.db.repository import insert, update_by_id
from jobbot.jobs.snapshots import upsert_snapshot
from jobbot.matching import (
    build_tfidf,
    compute_features,
    embed_texts,
    explain,
    l0,
    rank,
    ranking_text,
    use_injected_embedder,
)
from jobbot.matching.embed import vector_path
from jobbot.matching.features import bind_tfidf
from jobbot.matching.logistic import fit_predict
from jobbot.matching.store import count_match_runs, load_run, open_db
from jobbot.matching.tfidf import current
from jobbot.profile import add_manual_fact, confirm_fact, ensure_profile, freeze_profile, list_facts, save_preference

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_matching_state():
    use_injected_embedder(None)
    bind_tfidf(None, None)
    yield
    use_injected_embedder(None)
    bind_tfidf(None, None)


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


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("测试不应访问网络")

    monkeypatch.setattr(httpx, "Client", boom)


def _profile(statement: str, *, name: str = "合成考生", category: str = "skill"):
    profile_id = ensure_profile(name)
    fact = add_manual_fact(
        profile_id,
        category=category,
        statement=statement,
        excerpt=statement,
        source_text=statement,
    )
    confirm_fact(fact.id)
    snapshot = freeze_profile(profile_id)
    preference = save_preference(
        profile_id,
        [HardConstraint(field="city", operator="eq", value="北京")],
    )
    return profile_id, snapshot, preference


def _job(description: str, *, city: str | None, title: str, external_id: str):
    _job_row, snapshot, _created = upsert_snapshot(
        JobSnapshotDraft(
            platform="manual",
            source="manual",
            title=title,
            company="合成公司",
            city=city,
            description=description,
            external_id=external_id,
            availability="unknown",
        )
    )
    return snapshot


def _comparable(run) -> dict:
    payload = run.model_dump(mode="json")
    payload.pop("id", None)
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    for result in payload["results"]:
        result.pop("id", None)
        result.pop("run_id", None)
        result.pop("created_at", None)
        result.pop("updated_at", None)
    return payload


def _python_embedder(texts: list[str], kind: str) -> list[list[float]]:
    del kind
    vectors = []
    for text in texts:
        if "Python" in text or "python" in text:
            vectors.append([1.0, 0.0])
        else:
            vectors.append([0.0, 1.0])
    return vectors


def _save_parse(snapshot_id: str, requirements: list[Requirement]) -> None:
    with open_db() as connection:
        insert(
            connection,
            "jd_parse",
            {
                "snapshot_id": snapshot_id,
                "status": "ok",
                "requirements": [item.model_dump(mode="json") for item in requirements],
            },
        )


def _skill(requirement_id: str, text: str, skills: list[str], requirement_type: str = "core_skill") -> Requirement:
    return Requirement(
        id=requirement_id,
        text=text,
        type=requirement_type,  # type: ignore[arg-type]
        weight=2,
        source_ref=SourceRef(span=[0, max(len(text), 1)]),
        normalized=RequirementNormalized(skills=skills),
    )


def test_tfidf_fits_only_passed_corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile("档案句子")
    train = _job("abab", city="北京", title="训练", external_id="train-1")
    _job("zzzz", city="北京", title="未进语料", external_id="hold-1")
    index = build_tfidf([train.id], TfidfConfig())
    fitted = current()
    assert fitted is not None
    assert index.vocab_path
    assert "zz" not in fitted.idf
    assert fitted.transform("zzzz") == {}
    saved = json.loads(Path(index.vocab_path).read_text(encoding="utf-8"))
    assert saved["sklearn"] is None
    assert saved["implementation"] == "stdlib_char_ngram_tfidf"
    assert saved["config"]["analyzer"] == "char"
    assert saved["config"]["ngram_range"] == [2, 4]
    assert saved["config"]["norm"] == "l2"
    assert saved["config"]["sublinear_tf"] is False
    assert saved["config"]["min_df"] == 1


def test_b0_repeat_uses_snapshot_and_keeps_fail_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    text = "python-data-pipeline-aaaa"
    _profile_id, snapshot, preference = _profile(text)
    matched = _job(text, city="上海", title="文本近但城市不符", external_id="fail-1")
    other = _job("厨房洗碗保洁zzzz", city="北京", title="城市符合", external_id="pass-1")
    cfg = {"corpus_ids": [matched.id, other.id]}
    first = rank("b0", snapshot, [matched, other], preference, cfg)
    second = rank("b0", snapshot, [matched, other], preference, cfg)
    assert first.profile_snapshot_id == snapshot.id
    assert _comparable(first) == _comparable(second)
    stored = load_run(first.id)
    assert stored is not None
    assert stored.profile_snapshot_id == snapshot.id
    by_id = {item.snapshot_id: item for item in first.results}
    assert by_id[matched.id].hard_result == "fail"
    assert by_id[matched.id].rank is None
    assert by_id[matched.id].components["excluded_from_default_recommendation"] is True
    assert by_id[other.id].hard_result == "pass"
    assert by_id[other.id].rank == 1
    assert by_id[matched.id].score > by_id[other.id].score
    assert first.config["sklearn"] is None
    assert first.config["tfidf"]["implementation"] == "stdlib_char_ngram_tfidf"
    assert "scikit-learn" not in json.dumps(first.config, ensure_ascii=False)


def test_candidate_profile_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    profile_id = ensure_profile("合成考生")
    fact = add_manual_fact(
        profile_id,
        category="skill",
        statement="尚未确认的技能",
        excerpt="尚未确认的技能",
        source_text="尚未确认的技能",
    )
    preference = save_preference(profile_id, [])
    with open_db() as connection:
        snapshot_id = insert(
            connection,
            "profile_snapshot",
            {
                "profile_id": profile_id,
                "revision": 1,
                "fact_ids": [fact.id],
                "sha256": "candidate-snapshot",
            },
        )
    snapshot = ProfileSnapshot(
        id=snapshot_id,
        profile_id=profile_id,
        revision=1,
        fact_ids=[fact.id],
        sha256="candidate-snapshot",
    )
    before = count_match_runs()
    with pytest.raises(JobBotError, match="没有排序"):
        rank("b0", snapshot, [], preference, {})
    with pytest.raises(JobBotError, match="ProfileSnapshot"):
        rank("b0", fact, [], preference, {})  # type: ignore[arg-type]
    assert count_match_runs() == before


def test_missing_embedding_does_not_rank(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, preference = _profile("使用 Python3")
    job = _job("使用 Python3 整理数据", city="北京", title="数据", external_id="emb-1")
    before = count_match_runs()
    for method in ("b1", "b2", "b3"):
        with pytest.raises(JobBotError, match="没有排序") as caught:
            rank(method, snapshot, [job], preference, {"corpus_ids": [job.id]})
        assert "EMBEDDING_REVISION" in str(caught.value)
    assert count_match_runs() == before


def test_embedding_revisions_do_not_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, preference = _profile("档案句子使用 Python3")
    matched = _job("岗位需要 Python3 做数据整理", city="北京", title="匹配", external_id="rev-job-1")
    other = _job("食堂洗碗与保洁", city="北京", title="其他", external_id="rev-job-2")

    def embed_a(texts: list[str], kind: str) -> list[list[float]]:
        del kind
        return [[1.0, 0.0] if "Python" in text else [0.0, 1.0] for text in texts]

    def embed_b(texts: list[str], kind: str) -> list[list[float]]:
        del kind
        vectors = []
        for text in texts:
            if "档案句子" in text:
                vectors.append([1.0, 0.0])
            elif "Python" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([1.0, 0.0])
        return vectors

    use_injected_embedder(embed_a, "rev-a")
    first = rank("b1", snapshot, [matched, other], preference, {"corpus_ids": [matched.id, other.id]})
    stored_path = vector_path("rev-a", "doc", matched.description or "")
    original = stored_path.read_bytes()
    use_injected_embedder(embed_b, "rev-a")
    embed_texts([matched.description or ""], "doc")
    assert stored_path.read_bytes() == original
    use_injected_embedder(embed_b, "rev-b")
    second = rank("b1", snapshot, [matched, other], preference, {"corpus_ids": [matched.id, other.id]})
    assert stored_path.read_bytes() == original
    revised = vector_path("rev-b", "doc", matched.description or "")
    assert revised.is_file()
    assert revised.read_bytes() != original
    reloaded = load_run(first.id)
    assert reloaded is not None
    assert reloaded.config["embedding"]["revision"] == "rev-a"
    assert second.config["embedding"]["revision"] == "rev-b"
    first_score = {item.snapshot_id: item.score for item in reloaded.results}[matched.id]
    second_score = {item.snapshot_id: item.score for item in second.results}[matched.id]
    assert first_score == 1.0
    assert second_score == 0.5


def test_missing_features_stay_null_and_aliases_do_not_cross(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, preference = _profile("会写 Python3 脚本")
    job = _job("需要 Python，不接受 JavaScript 充数", city=None, title="技能", external_id="skill-1")
    parse = JDParse(
        snapshot_id=job.id,
        status="ok",
        requirements=[_skill("py", "Python", ["Python"])],
    )
    python_features = compute_features(snapshot, job, parse, preference)
    assert python_features.f_skill == 1.0
    assert python_features.f_pref is None
    assert python_features.f_exp is None
    profile_id = ensure_profile("合成考生")
    statement = "日常使用 JavaScript"
    fact = add_manual_fact(
        profile_id,
        category="skill",
        statement=statement,
        excerpt=statement,
        source_text=statement,
    )
    confirm_fact(fact.id)
    java_snapshot = freeze_profile(profile_id)
    java_preference = save_preference(profile_id, [])
    java_job = _job("日常使用 JavaScript", city="北京", title="前端", external_id="skill-2")
    java_features = compute_features(
        java_snapshot,
        java_job,
        JDParse(
            snapshot_id=java_job.id,
            status="ok",
            requirements=[_skill("java", "Java", ["Java"])],
        ),
        java_preference,
    )
    assert java_features.f_skill == 0.0
    soft = Preference(
        id=preference.id,
        profile_id=preference.profile_id,
        revision=preference.revision,
        hard=preference.hard,
        soft=[{"field": "city", "value": "北京"}],
    )
    assert compute_features(snapshot, job, parse, soft).f_pref is None


def test_b2_renormalizes_missing_weights(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, preference = _profile("会写 Python3 脚本")
    job = _job("岗位使用 Python3 完成数据整理", city="上海", title="数据岗", external_id="b2-1")
    use_injected_embedder(_python_embedder, "rev-b2")
    bare_preference = preference.model_copy(update={"hard": []})
    bare = rank("b2", snapshot, [job], bare_preference, {"corpus_ids": [job.id]})
    result = bare.results[0]
    assert result.components["features"]["f_skill"] is None
    assert result.components["features"]["f_pref"] is None
    assert result.components["features"]["f_resp"] is None
    assert result.components["missing_features"] == ["f_skill", "f_resp", "f_pref"]
    assert result.components["weights_renormalized"] is True
    assert "没有用 0" in result.components["weight_note"]
    assert result.score == result.components["features"]["f_sem"]
    duty = _skill("duty", "使用 Python3 完成数据整理", [], requirement_type="responsibility")
    skill = _skill("skill", "Python", ["Python"])
    city_job = _job("岗位使用 Python3 完成数据整理。", city="北京", title="在北京", external_id="b2-2")
    _save_parse(city_job.id, [skill, duty])
    preference_soft = save_preference(
        snapshot.profile_id,
        [],
        soft=[{"field": "city", "value": "北京"}],
    )
    weighted = rank(
        "b2",
        snapshot,
        [city_job],
        preference_soft,
        {"corpus_ids": [job.id, city_job.id]},
    )
    features = weighted.results[0].components["features"]
    assert weighted.results[0].components["weights_renormalized"] is False
    expected = (
        0.45 * features["f_sem"]
        + 0.25 * features["f_skill"]
        + 0.20 * features["f_resp"]
        + 0.10 * features["f_pref"]
    )
    assert weighted.results[0].score == pytest.approx(expected)
    assert features["f_pref"] == 1.0
    for value in features.values():
        if value is not None:
            assert 0.0 <= value <= 1.0


def test_b3_is_directional_and_repeatable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, _preference = _profile("会写 Python3 脚本")
    preference = save_preference(snapshot.profile_id, [])
    positive = [
        _job(
            f"岗位使用 Python3 完成数据整理 {index}",
            city="北京",
            title=f"正{index}",
            external_id=f"pos-{index}",
        )
        for index in (1, 2)
    ]
    negative = [
        _job(f"食堂洗碗与保洁 {index}", city="北京", title=f"负{index}", external_id=f"neg-{index}")
        for index in (1, 2)
    ]
    snaps = positive + negative
    labels = [{"snapshot_id": snap.id, "rating": 3} for snap in positive]
    labels += [{"snapshot_id": snap.id, "rating": 0} for snap in negative]
    cfg = {"corpus_ids": [snap.id for snap in snaps], "labels": labels, "seed": 11, "C": 1.0}
    use_injected_embedder(_python_embedder, "rev-b3")
    first = rank("b3", snapshot, snaps, preference, cfg)
    second = rank("b3", snapshot, snaps, preference, cfg)
    assert _comparable(first) == _comparable(second)
    assert first.profile_snapshot_id == snapshot.id
    note = first.config["logistic"]["note"]
    assert "方向性实验" in note
    assert "系统学习了用户偏好" not in json.dumps(first.config, ensure_ascii=False)
    assert first.config["logistic"]["seed"] == 11
    assert first.config["logistic"]["sklearn"] is None
    assert first.config["sklearn"] is None
    for result in first.results:
        assert result.score is not None
        assert 0.0 <= result.score <= 1.0
        assert result.components["features"]["f_pref"] is None
        assert result.components["directional_experiment"] is True
    positive_ids = {snap.id for snap in positive}
    positives = [item.score for item in first.results if item.snapshot_id in positive_ids]
    negatives = [item.score for item in first.results if item.snapshot_id not in positive_ids]
    assert sum(positives) / len(positives) > sum(negatives) / len(negatives)


def test_l0_uses_gateway_and_records_nondeterminism(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _block_network(monkeypatch)
    _profile_id, snapshot, preference = _profile("会写 Python3 脚本")
    job = _job("岗位使用 Python3", city="北京", title="模型", external_id="l0-1")
    calls: list[str] = []

    def fake_call(purpose, template, variables, schema, budget=None):
        del template, budget
        calls.append(purpose)
        assert variables["temperature"] == 0
        encoded = json.dumps(variables, ensure_ascii=False)
        assert "性别" not in encoded
        assert "婚" not in encoded
        parsed = schema.model_validate({"rating": 2, "reason": "方向接近"})
        return LLMResult(status="ok", purpose=purpose, parsed=parsed, cache_hit=False)

    monkeypatch.setattr("jobbot.llm.gateway.call", fake_call)
    finished = l0(snapshot, [job], preference, {})
    assert calls == ["match_l0"]
    assert finished.method == "llm"
    assert finished.profile_snapshot_id == snapshot.id
    assert finished.config["temperature"] == 0
    assert finished.config["reproducible_like_b0_b3"] is False
    assert "不保证" in finished.config["note"]
    assert finished.results[0].score == 2
    assert finished.results[0].components["temperature"] == 0


def test_explain_uses_saved_evidence_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, preference = _profile("会写 Python3 脚本")
    job = _job("需要 Python", city="北京", title="解释", external_id="ex-1")
    _save_parse(job.id, [_skill("py", "Python", ["Python"])])
    finished = rank("b0", snapshot, [job], preference, {"corpus_ids": [job.id]})
    result = finished.results[0]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("explain 不应重新计算特征")

    monkeypatch.setattr("jobbot.matching.features.compute_features", forbidden)
    explanation = explain(result)
    assert explanation.used_template is False
    assert explanation.score_note == EXPLANATION_SCORE_NOTE
    assert any("Python" in line for line in explanation.matched)
    assert all("录用概率" not in line for line in explanation.matched)
    broken = result.model_copy(deep=True)
    broken.evidence["matched"][0]["fact_id"] = "ab" * 16
    broken.evidence["matched"][0]["excerpt"] = "这段摘录不在事实里"
    fallback = explain(broken)
    assert fallback.used_template is True
    assert "匹配度较高" in fallback.matched[0]
    assert "这段摘录不在事实里" not in " ".join(fallback.matched + fallback.gaps + fallback.unknowns)


def test_demographic_attributes_are_not_ranking_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    profile_id = ensure_profile("合成考生")
    skill = "会写 Python3 脚本"
    personal = "性别男，年龄30，已婚"
    for statement, category in ((skill, "skill"), (personal, "other"), ("合成考生会写说明", "skill")):
        fact = add_manual_fact(
            profile_id,
            category=category,
            statement=statement,
            excerpt=statement,
            source_text=statement,
        )
        confirm_fact(fact.id)
    snapshot = freeze_profile(profile_id)
    preference = save_preference(profile_id, [])
    rendered = ranking_text(list_facts(profile_id), "合成考生")
    assert "性别" not in rendered
    assert "年龄" not in rendered
    assert "已婚" not in rendered
    assert "合成考生" not in rendered
    assert "Python3" in rendered
    job = _job(skill, city="北京", title="人口属性", external_id="demo-1")
    finished = rank("b0", snapshot, [job], preference, {"corpus_ids": [job.id]})
    encoded = json.dumps(finished.results[0].evidence, ensure_ascii=False)
    assert personal not in encoded


def test_logistic_learns_direction() -> None:
    rows = []
    labels = []
    for value, label in ((0.0, 0), (0.1, 0), (0.9, 1), (1.0, 1)):
        rows.append(
            {
                "f_sem": value,
                "f_skill": None,
                "f_resp": None,
                "f_pref": None,
                "f_tfidf": None,
                "f_exp": None,
            }
        )
        labels.append(label)
    scores, report = fit_predict(rows, labels, c=1.0, seed=3)
    assert report["sklearn"] is None
    assert report["seed"] == 3
    assert scores[2] > scores[1]
    again, _report = fit_predict(rows, labels, c=1.0, seed=3)
    assert scores == again


def test_e1_script_recomputes_without_writing_results(tmp_path: Path) -> None:
    rows = [
        {
            "method": "b0",
            "profile_id": "p",
            "snapshot_id": "a",
            "rating": 3,
            "rank": 1,
            "score": 0.9,
            "hard_result": "pass",
            "hard_label": "pass",
        },
        {
            "method": "b0",
            "profile_id": "p",
            "snapshot_id": "b",
            "rating": 0,
            "rank": 2,
            "score": 0.1,
            "hard_result": "fail",
            "hard_label": "fail",
        },
        {
            "method": "b0",
            "profile_id": "q",
            "snapshot_id": "c",
            "rating": 0,
            "rank": 1,
            "score": 0.2,
            "hard_result": "needs_review",
            "hard_label": "unclear",
        },
    ]
    path = tmp_path / "per-query-results.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("run_e1", ROOT / "eval" / "run_e1.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metrics = module.recompute_metrics(rows)
    assert metrics["ndcg_at_10"]["macro_mean"] == pytest.approx(1.0)
    assert metrics["p_at_10"]["macro_mean"] == pytest.approx(0.25)
    assert metrics["ndcg_at_10"]["excluded_no_relevant"] == [{"method": "b0", "profile_id": "q"}]
    assert metrics["hard_false_pass_rate"] == 0.0
    assert metrics["hard_false_fail_rate"] == 0.0
    assert metrics["needs_review_rate"] == pytest.approx(1 / 3)
    assert module.main(["--per-query", str(path)]) == 0
    assert module.main(["--per-query", str(tmp_path / "missing.jsonl")]) == 1
    assert not (ROOT / "eval" / "results").exists()
    assert (2**3 - 1) / math.log2(2) == 7


def test_methods_repeat_on_the_same_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    _profile_id, snapshot, _preference = _profile("会写 Python3 脚本")
    preference = save_preference(
        snapshot.profile_id,
        [],
        soft=[{"field": "keyword", "value": "Python"}],
    )
    first_job = _job("岗位使用 Python3 完成数据整理", city="北京", title="甲", external_id="all-1")
    second_job = _job("食堂洗碗与保洁", city="北京", title="乙", external_id="all-2")
    snaps = [first_job, second_job]
    labels = [
        {"snapshot_id": first_job.id, "rating": 3},
        {"snapshot_id": second_job.id, "rating": 0},
    ]
    use_injected_embedder(_python_embedder, "rev-all")
    cfg = {"corpus_ids": [snap.id for snap in snaps], "labels": labels, "seed": 5}
    for method in ("b0", "b1", "b2", "b3"):
        left = rank(method, snapshot, snaps, preference, cfg)
        right = rank(method, snapshot, snaps, preference, cfg)
        assert left.profile_snapshot_id == snapshot.id
        assert right.profile_snapshot_id == snapshot.id
        assert _comparable(left) == _comparable(right)


def test_experience_feature_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    profile_id = ensure_profile("合成考生")
    statement = "2020年到2023年做数据整理"
    fact = add_manual_fact(
        profile_id,
        category="work",
        statement=statement,
        excerpt=statement,
        source_text=statement,
    )
    with open_db() as connection:
        update_by_id(
            connection,
            "fact",
            fact.id,
            {
                "structured": FactStructured(
                    org="合成公司",
                    role="工程师",
                    start="2020",
                    end="2023",
                    skills=["Python3"],
                    metrics=[],
                ).model_dump(mode="json")
            },
        )
    education = "本科毕业于合成大学"
    school = add_manual_fact(
        profile_id,
        category="education",
        statement=education,
        excerpt=education,
        source_text=education,
    )
    confirm_fact(fact.id)
    confirm_fact(school.id)
    snapshot = freeze_profile(profile_id)
    preference = save_preference(profile_id, [])
    job = _job("负责数据整理", city="北京", title="经验", external_id="exp-1")
    drafted = job.model_copy(update={"experience_text": "3年以上", "degree_text": "本科"})
    features = compute_features(
        snapshot,
        drafted,
        JDParse(snapshot_id=job.id, status="needs_manual", requirements=None),
        preference,
    )
    assert features.f_exp == pytest.approx(1.0)


def test_match_page_guides_when_empty_and_can_rank_b0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    _prepare_env(monkeypatch, tmp_path)
    _block_network(monkeypatch)

    def visible(app: object) -> str:
        chunks: list[str] = []
        for element_name in ("title", "header", "subheader", "markdown", "warning", "error", "info", "caption", "text"):
            for element in getattr(app, element_name):
                chunks.append(str(element.value))
        return "\n".join(chunks)

    empty = AppTest.from_file(str(ROOT / "app" / "views" / "match.py"))
    empty.run(timeout=30)
    assert not empty.exception, empty.exception
    empty_text = visible(empty)
    assert "还没有档案" in empty_text
    assert "没有排序" in empty_text
    assert "排序记录已写入" not in empty_text
    assert "成功" not in empty_text

    _profile("会写 Python3 脚本")
    _job("岗位使用 Python3 完成数据整理", city="北京", title="合成岗位", external_id="ui-1")
    page = AppTest.from_file(str(ROOT / "app" / "views" / "match.py"))
    page.run(timeout=30)
    assert not page.exception, page.exception
    before = visible(page)
    assert "这次会话还没有运行排序" in before
    assert "方向性实验" in before
    assert "排序记录已写入" not in before
    page.button[0].click().run(timeout=30)
    assert not page.exception, page.exception
    after = visible(page)
    assert "排序记录已写入" in after
    assert "不是录用概率" in after
    assert "默认推荐" in after
    assert "成功" not in after
    assert "系统学习了用户偏好" not in after
