from jobbot.capabilities import capability_matrix


def _by_name(**kwargs: object) -> dict[str, object]:
    rows = capability_matrix(
        llm_api_key=str(kwargs["llm_api_key"]),
        llm_base_url=str(kwargs.get("llm_base_url", "")),
        llm_model=str(kwargs.get("llm_model", "")),
        embedding_revision=str(kwargs["embedding_revision"]),
        embedding_model_exists=bool(kwargs["embedding_model_exists"]),
        collector_implemented=bool(kwargs["collector_implemented"]),
    )
    return {row.name: row for row in rows}


def test_no_key_matches_degradation_table() -> None:
    rows = _by_name(
        llm_api_key="",
        embedding_revision="",
        embedding_model_exists=False,
        collector_implemented=False,
    )
    unavailable = {"自动抽取", "解释", "草稿生成", "B1", "B2", "B3", "Boss 自动采集"}
    available = {"手动录入事实", "JD 粘贴", "导入 JSON", "B0", "规则校验", "手动材料编辑"}
    for name in unavailable:
        assert rows[name].status == "不可用", name
    for name in available:
        assert rows[name].status == "可用", name
    assert rows["运行模式"].status == "降级"
    assert "未配置 LLM_API_KEY" in rows["自动抽取"].reason
    assert "EMBEDDING_REVISION 为空" in rows["B1"].reason
    assert "采集器尚未实现" in rows["Boss 自动采集"].reason
    assert "粘贴" in rows["JD 粘贴"].reason
    for row in rows.values():
        assert row.reason
        if "录用概率" in row.reason:
            assert "不是录用概率" in row.reason


def test_b1_without_key_is_available_only_when_embedding_exists() -> None:
    missing = _by_name(
        llm_api_key="",
        embedding_revision="rev-1",
        embedding_model_exists=False,
        collector_implemented=False,
    )
    assert missing["B1"].status == "不可用"
    assert "模型文件不在" in missing["B1"].reason
    assert missing["B0"].status == "可用"

    ready = _by_name(
        llm_api_key="",
        embedding_revision="rev-1",
        embedding_model_exists=True,
        collector_implemented=False,
    )
    assert ready["B1"].status == "可用"
    assert ready["B2"].status == "可用"
    assert ready["B3"].status == "可用"
    assert ready["自动抽取"].status == "不可用"
    assert "无 LLM Key" in ready["B1"].reason
