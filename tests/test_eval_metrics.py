"""评测指标与 E0–E5 脚本。使用合成输入，不写 eval/results。"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import metrics
import run_all
import run_e0
import run_e1
import run_e2
import run_e3
import run_e4
import run_e5


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _spec_dcg(ratings: list[float]) -> float:
    total = 0.0
    for rank, rating in enumerate(ratings, start=1):
        total += (2**rating - 1) / math.log2(rank + 1)
    return total


def _spec_ndcg(ratings: list[float]) -> float | None:
    if not ratings or all(rating < 2 for rating in ratings):
        return None
    limit = min(10, len(ratings))
    ideal = _spec_dcg(sorted(ratings, reverse=True)[:limit])
    if ideal == 0:
        return None
    return _spec_dcg(ratings[:limit]) / ideal


def _spec_precision(ratings: list[float]) -> float:
    limit = min(10, len(ratings))
    return sum(1 for rating in ratings[:limit] if rating >= 2) / limit


def _ranking_rows() -> tuple[list[dict], dict[str, list[float]]]:
    queries = {
        "long": [3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
        "zeros": [0, 0],
        "low": [1, 0],
        "hit": [2],
    }
    rows: list[dict] = []
    for profile_id, ratings in queries.items():
        for rank, rating in reversed(list(enumerate(ratings, start=1))):
            rows.append(
                {
                    "method": "b2",
                    "profile_id": profile_id,
                    "snapshot_id": f"{profile_id}-{rank}",
                    "rank": rank,
                    "rating": rating,
                    "score": 1 / rank,
                    "hard_label": "pass" if rating >= 2 else "fail",
                    "hard_result": "pass" if rating >= 2 else "fail",
                }
            )
    return rows, queries


def _assert_no_results(*paths: Path) -> None:
    assert not (ROOT / "eval" / "results").exists()
    for path in paths:
        assert not path.exists()
        assert not (path.parent / "metrics.json").exists()


def test_dcg_gain_and_discount() -> None:
    assert metrics.dcg([3]) == pytest.approx((2**3 - 1) / math.log2(1 + 1))
    assert metrics.dcg([3, 1]) == pytest.approx(
        (2**3 - 1) / math.log2(2) + (2**1 - 1) / math.log2(3)
    )


def test_ndcg_and_precision_follow_section_7() -> None:
    rows, queries = _ranking_rows()
    first = metrics.recompute_metrics(rows)
    second = metrics.recompute_metrics(list(reversed(rows)))
    assert metrics.max_numeric_diff(first, second) <= 1e-9
    by_profile = {item["profile_id"]: item for item in first["ndcg_at_10"]["per_query"]}
    included: list[float] = []
    precisions: list[float] = []
    for profile_id, ratings in queries.items():
        expected_ndcg = _spec_ndcg(ratings)
        if expected_ndcg is None:
            assert by_profile[profile_id]["ndcg_at_10"] is None
        else:
            assert by_profile[profile_id]["ndcg_at_10"] == pytest.approx(expected_ndcg)
            included.append(expected_ndcg)
        assert by_profile[profile_id]["p_at_10"] == pytest.approx(_spec_precision(ratings))
        precisions.append(_spec_precision(ratings))
        if len(ratings) < 10:
            assert by_profile[profile_id]["n"] == len(ratings)
    assert first["ndcg_at_10"]["excluded_count"] == 2
    assert first["ndcg_at_10"]["macro_mean"] == pytest.approx(sum(included) / len(included))
    assert first["p_at_10"]["macro_mean"] == pytest.approx(sum(precisions) / len(precisions))
    assert first["p_at_10"]["short_query_count"] == 3
    assert {item["profile_id"] for item in first["ndcg_at_10"]["excluded_no_relevant"]} == {"zeros", "low"}
    naive = (sum(included) + 0 + 0) / (len(included) + 2)
    assert abs(first["ndcg_at_10"]["macro_mean"] - naive) > 1e-9


def test_t21_recomputes_twice_and_missing_file_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    rows, _queries = _ranking_rows()
    path = tmp_path / "per-query-results.jsonl"
    _write_jsonl(path, rows)
    assert run_e1.main(["--per-query", str(path)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert run_e1.main(["--per-query", str(path)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert metrics.max_numeric_diff(first, second) <= 1e-9
    assert first == metrics.recompute_metrics(rows)
    missing = tmp_path / "missing-per-query-results.jsonl"
    assert run_e1.main(["--per-query", str(missing)]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "not_run"
    assert error["wrote_results"] is False
    assert str(missing) in error["missing"]
    assert str(missing) in error["error"]
    _assert_no_results(tmp_path / "metrics.json")


def test_e0_counts_modes_separately() -> None:
    rows = [
        {
            "mode": "A",
            "clicked": True,
            "captured": True,
            "parsed_ok": True,
            "fields": {"job_name": True, "post_description": False},
            "salary_readable": True,
            "elapsed_ms": 10,
        },
        {
            "mode": "assist",
            "clicked": True,
            "captured": True,
            "parsed_ok": False,
            "salary_readable": False,
            "elapsed_ms": 30,
            "stop_reason": "login_page",
        },
        {
            "mode": "B",
            "clicked": True,
            "captured": False,
            "parsed_ok": False,
            "salary_readable": None,
            "elapsed_ms": None,
        },
    ]
    counted = metrics.count_e0(rows)
    mode_a = counted["by_mode"]["A"]
    mode_b = counted["by_mode"]["B"]
    assert mode_a["n_clicked"] == 2
    assert mode_a["n_captured"] == 2
    assert mode_a["n_parsed_ok"] == 1
    assert mode_a["capture_success_rate"] == pytest.approx(0.5)
    assert mode_a["field_nonempty_rate"] == {"job_name": 1.0, "post_description": 0.0}
    assert mode_a["salary_readable_rate"] == pytest.approx(0.5)
    assert mode_a["stop_reason_counts"] == {"login_page": 1}
    assert mode_a["mean_elapsed_ms"] == pytest.approx(20)
    assert mode_b["capture_success_rate"] == pytest.approx(0.0)
    assert mode_b["salary_readable_rate"] is None
    assert mode_b["mean_elapsed_ms"] is None
    assert "capture_success_rate" not in counted


def test_e2_counts_precision_recall_and_jd() -> None:
    rows = [
        {"record_type": "fact", "label": "正确", "citation_valid": True},
        {"record_type": "fact", "label": "correct", "citation_valid": True},
        {"record_type": "fact", "label": "部分", "citation_valid": False},
        {"record_type": "fact", "label": "错误"},
        {"record_type": "fact", "label": "幻觉"},
        {"record_type": "missed"},
        {"record_type": "jd_requirement", "type_correct": True, "inferred_marked_hard": False},
        {"record_type": "jd_requirement", "type_correct": True, "inferred_marked_hard": True},
        {"record_type": "jd_requirement", "type_correct": False, "inferred_marked_hard": True},
    ]
    counted = metrics.count_e2(rows)
    assert counted["fact_precision"] == pytest.approx(2 / 5)
    assert counted["fact_recall_estimate"] == pytest.approx(2 / 3)
    assert counted["n_missed"] == 1
    assert counted["citation_valid_rate"] == pytest.approx(2 / 3)
    assert counted["jd_type_accuracy"] == pytest.approx(2 / 3)
    assert counted["hard_mislabel_count"] == 2


def test_e3_keeps_unparseable_samples_in_the_rate() -> None:
    rows = [
        {"record_type": "claim", "condition": "R2", "factual": True, "supported": True},
        {"record_type": "claim", "condition": "R2", "factual": True, "supported": False},
        {"record_type": "claim", "condition": "R2", "unparseable": True},
        {"record_type": "requirement", "condition": "R2", "important": True, "covered": True},
        {"record_type": "requirement", "condition": "R2", "important": True, "covered": False},
    ]
    counted = metrics.count_e3(rows)["by_condition"]["R2"]
    assert counted["unsupported_statement_rate"] == pytest.approx(2 / 3)
    assert counted["important_requirement_coverage"] == pytest.approx(0.5)
    assert counted["mean_blind_score"] is None
    assert counted["edit_rate"] is None
    assert counted["mean_elapsed_ms"] is None
    assert counted["n_calls"] is None
    assert counted["cost_usd"] is None


def test_e4_agreement_without_inventing_duration() -> None:
    rows = [
        {"arm": "人工", "profile_id": "p", "job_id": "j1", "decision": "apply", "elapsed_ms": 100},
        {"arm": "系统", "profile_id": "p", "job_id": "j1", "decision": "apply", "elapsed_ms": 40},
        {"arm": "manual", "profile_id": "p", "job_id": "j2", "decision": "skip"},
        {"arm": "system", "profile_id": "p", "job_id": "j2", "decision": "apply"},
    ]
    counted = metrics.count_e4(rows)
    assert counted["decision_agreement_rate"] == pytest.approx(0.5)
    assert counted["n_paired_jobs"] == 2
    assert counted["n_profiles"] == 1
    assert counted["by_arm"]["manual"]["mean_elapsed_ms"] == pytest.approx(100)
    assert counted["by_arm"]["manual"]["n_elapsed"] == 1
    assert counted["by_arm"]["system"]["mean_elapsed_ms"] == pytest.approx(40)


def test_e5_rates_and_perturbations_are_not_written(tmp_path: Path) -> None:
    rows = [
        {"method": "V0", "split": "扰动", "kind": "数字", "gold_unsupported": True, "predicted_unsupported": True},
        {"method": "V0", "split": "perturbation", "kind": "number", "gold_unsupported": True, "predicted_unsupported": True},
        {"method": "V0", "split": "perturbation", "kind": "verb", "gold_unsupported": True, "predicted_unsupported": False},
        {"method": "V0", "split": "对照", "kind": None, "gold_unsupported": False, "predicted_unsupported": False},
        {"method": "V0", "split": "control", "gold_unsupported": False, "predicted_unsupported": False},
        {"method": "V0", "split": "control", "gold_unsupported": False, "predicted_unsupported": False},
        {"method": "V0", "split": "control", "gold_unsupported": False, "predicted_unsupported": True},
    ]
    counted = metrics.count_e5(rows)["by_method"]["V0"]
    assert counted["perturbation"]["precision"] == pytest.approx(1)
    assert counted["perturbation"]["recall"] == pytest.approx(2 / 3)
    assert counted["perturbation"]["f1"] == pytest.approx(0.8)
    assert counted["by_kind"]["number"]["recall"] == pytest.approx(1)
    assert counted["by_kind"]["verb"]["recall"] == pytest.approx(0)
    assert counted["by_kind"]["verb"]["precision"] is None
    assert counted["control_false_positive_rate"] == pytest.approx(0.25)
    source = "2021年在星河公司参与数据整理"
    assert "2022" in run_e5.perturb_statement(source, "number")
    assert "主导" in run_e5.perturb_statement(source, "verb")
    assert "Kubernetes" in run_e5.perturb_statement(source, "skill")
    assert "1999" in run_e5.perturb_statement(source, "org_or_date")
    assert "另一公司" in run_e5.perturb_statement("在星河公司参与整理", "org_or_date")
    assert "因此独立促成" in run_e5.perturb_statement(source, "causal")
    for kind in run_e5.PERTURBATION_KINDS:
        assert run_e5.perturb_statement(source, kind) != source
    with pytest.raises(ValueError):
        run_e5.perturb_statement(source, "unknown")
    assert list(tmp_path.iterdir()) == []
    _assert_no_results(tmp_path / "metrics.json")


def test_scripts_fail_closed_and_recompute_when_input_exists(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ranking, _queries = _ranking_rows()
    e0_rows = [
        {
            "mode": "A",
            "clicked": True,
            "captured": True,
            "parsed_ok": True,
            "fields": {"job_name": "合成岗位"},
            "salary_readable": True,
            "elapsed_ms": 15,
        }
    ]
    e2_rows = [{"record_type": "fact", "label": "correct", "citation_valid": True}]
    e3_rows = [{"record_type": "claim", "condition": "R0", "factual": True, "supported": True}]
    e4_rows = [
        {"arm": "manual", "profile_id": "p", "job_id": "j", "decision": "apply"},
        {"arm": "system", "profile_id": "p", "job_id": "j", "decision": "skip"},
    ]
    e5_rows = [
        {
            "method": "V0",
            "split": "control",
            "gold_unsupported": False,
            "predicted_unsupported": False,
        }
    ]
    cases = [
        (run_e0, "--session-log", "session-log.jsonl", e0_rows, metrics.count_e0),
        (run_e1, "--per-query", "per-query-results.jsonl", ranking, metrics.recompute_metrics),
        (run_e2, "--audit", "audit.jsonl", e2_rows, metrics.count_e2),
        (run_e3, "--audit", "audit-e3.jsonl", e3_rows, metrics.count_e3),
        (run_e4, "--sessions", "sessions.jsonl", e4_rows, metrics.count_e4),
        (run_e5, "--judgments", "judgments.jsonl", e5_rows, metrics.count_e5),
    ]
    for module, flag, filename, rows, compute in cases:
        missing = tmp_path / f"missing-{filename}"
        assert module.main([flag, str(missing)]) == 1
        error = json.loads(capsys.readouterr().err)
        assert error["wrote_results"] is False
        assert str(missing) in error["error"]
        present = tmp_path / filename
        _write_jsonl(present, rows)
        assert module.main([flag, str(present)]) == 0
        payload = json.loads(capsys.readouterr().out)
        if module is run_e1:
            assert payload == compute(rows)
        else:
            assert payload.pop("status") == "recomputed"
            assert payload.pop("wrote_results") is False
            assert payload == compute(rows)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert run_e1.main(["--per-query", str(empty)]) == 1
    assert "空" in json.loads(capsys.readouterr().err)["error"]
    _assert_no_results(tmp_path / "metrics.json")


def test_run_all_is_not_run_without_real_inputs(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert run_all.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["wrote_results"] is False
    for experiment_id in ("E0", "E1", "E1a", "E2", "E3", "E4", "E5"):
        item = report["experiments"][experiment_id]
        assert item["status"] == "not_run"
        assert item["missing"]
        assert item["missing"][0] in item["reason"]
    assert run_e0.main([]) == 1
    assert "session-log.jsonl" in json.loads(capsys.readouterr().err)["error"]
    ranking, _queries = _ranking_rows()
    e1_path = tmp_path / "per-query-results.jsonl"
    _write_jsonl(e1_path, ranking)
    assert run_all.main(["--e1", str(e1_path)]) == 1
    partial = json.loads(capsys.readouterr().out)
    assert partial["experiments"]["E1"]["status"] == "recomputed"
    assert partial["experiments"]["E1"]["metrics"]["ndcg_at_10"]["excluded_count"] == 2
    for experiment_id in ("E0", "E1a", "E2", "E3", "E4", "E5"):
        assert partial["experiments"][experiment_id]["status"] == "not_run"
    _assert_no_results(tmp_path / "metrics.json")
