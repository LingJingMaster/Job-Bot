# Boss 详情合成样例

合成，不是真实抓取。

这些 JSON 用明显的假编号（900001–900005）和假文案，只给 `scripts/spike_boss.py --from-json` 与 `tests/test_spike_boss.py` 做字段完整度验收。它们不是 Boss 页面返回的响应，不能用来判断 `/wapi/zpgeek/job/detail.json` 现在是否仍可用，也不能当作 go/no-go 证据。

文件里的 `bossInfo.name` 与 `encryptUserId` 是故意放进来的假字段，用来确认脚本在写入 `data/raw/spike/` 之前会丢掉它们。本目录不是采集输出。

| 文件 | 覆盖情况 |
|---|---|
| `complete.json` | HTTP 200、业务 code 0、`jobInfo` 含 `jobName` 与 `postDescription` |
| `missing_job_name.json` | 缺 `jobName` |
| `missing_post_description.json` | 缺 `postDescription` |
| `unreadable_salary.json` | `salaryDesc` 含私有区字符 |
| `business_failure.json` | HTTP 503，且业务 code 为 900001 |
| `html_entities.json` | 合成 HTML 实体，解码后才是可读文本 |
| `empty_post_description.json` | `postDescription` 只有空白 |
| `missing_optional.json` | 只有岗位名、职位描述和 encryptId，其余字段不出现 |
| `zpdata_empty.json` | HTTP 200、业务 code 0、`zpData` 为 `{}` |
| `business_code.json` | HTTP 200、业务 code 为 7 |
