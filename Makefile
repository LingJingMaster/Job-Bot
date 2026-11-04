.PHONY: setup ui collect test eval clean-data spike

SHELL := /bin/bash

setup:
	set -eu -o pipefail; \
	uv sync --frozen --all-groups --python 3.12; \
	mkdir -p data/db data/raw data/browser-profile data/cache data/exports; \
	revision="$$(uv run python -c 'from jobbot.config import load_config; print(load_config().embedding_revision)')"; \
	if [ -z "$$revision" ]; then \
		echo "EMBEDDING_REVISION 为空，不下载 embedding 模型。B1/B2/B3 需要 WP-D 锁定 revision 并放入模型文件后才可用；本次 setup 以 0 退出。"; \
		exit 0; \
	fi; \
	echo "EMBEDDING_REVISION=$$revision。本基础包不下载 embedding 模型。请把模型放到 data/models/$$revision（由 WP-D 完成）。"; \
	exit 0

ui:
	uv run streamlit run app/main.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false

collect:
	if [ ! -f src/jobbot/collector/runner.py ]; then \
		echo "采集器尚未实现：缺少 src/jobbot/collector/runner.py（WP-A）。未启动浏览器，数据未改变。" >&2; \
		exit 1; \
	fi; \
	uv run python -m jobbot.collector.runner

test:
	uv run pytest

eval:
	if [ ! -f eval/run_all.py ]; then \
		echo "评测尚未实现：缺少 eval/run_all.py（WP-G）。未写入实验结果。" >&2; \
		exit 1; \
	fi; \
	uv run python eval/run_all.py

clean-data:
	set -eu -o pipefail; \
	mkdir -p data; \
	data_real="$$(cd data && pwd -P)"; \
	repo_real="$$(pwd -P)"; \
	if [ "$$data_real" != "$$repo_real/data" ]; then \
		echo "拒绝清理：$$data_real 不是本仓库的 data/。" >&2; \
		exit 1; \
	fi; \
	find "$$data_real" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; \
	echo "已清除 data/ 下的内容。仓库其他文件未删除。"

spike:
	if [ ! -f scripts/spike_boss.py ]; then \
		echo "spike 脚本不存在：scripts/spike_boss.py 尚未提供（WP-A）。未访问 Boss。" >&2; \
		exit 1; \
	fi; \
	uv run python scripts/spike_boss.py
