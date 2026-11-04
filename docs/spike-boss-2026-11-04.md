# Boss 详情接口 D1 spike 记录（2026-11-04）

对照 `docs/02-architecture.md` §3.4 与 `docs/03-data-and-interfaces.md` §6。§6 的字段路径仍是 `unverified`。本次没有新的真实响应可以核对。

## 真实登录采集

状态：`not_run`

本次没有启动浏览器，没有人工登录，没有监听真实页面发出的 `GET /wapi/zpgeek/job/detail.json`，也没有把真实响应写入 `data/raw/spike/`。

真实采集要由人在可见浏览器里手动登录，并自己点开岗位。这次只交付了离线脚本、合成 fixture 和单元测试，没有做那一步。

## go / no-go

不能下结论。

§3.4 的 go 需要至少 10 条真实详情，其中不少于 8 条是可解析的完整 `jobInfo`。这些真实响应不存在，所以不能记 go，也不能记 no-go。`fixtures/boss_detail/` 里的 JSON 是合成数据，不是真实抓取。它们不能说明 Boss 详情接口仍然可用，也不能说明它不可用。

## 离线 fixture 测试

这组测试只检查本地合成文件和 `scripts/spike_boss.py` 的源码约束。它不是采集结果。

指定命令（仓库根目录）：

```bash
python -m unittest tests/test_spike_boss.py
```

当前环境的 PATH 里没有 `python`。同一条测试用 `python3`（Python 3.15.2）执行：

```bash
python3 -m unittest tests/test_spike_boss.py
```

结果：

```text
...........
----------------------------------------------------------------------
Ran 11 tests in 0.013s

OK
```

断言覆盖：完整合成样例计为可解析；缺少 `jobName` 或 `postDescription` 计为失败；`salaryDesc` 中的不可读字符会写进报告；脚本源码不含 `btn-startchat`，也没有对 send/upload 做 click 或 fill。接口字段是否仍与 §6 一致，仍然是 `unverified`。
