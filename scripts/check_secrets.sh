#!/usr/bin/env bash
# 扫描即将提交的文件。发现 API Key、Cookie 样式，或 data/ 被跟踪时以非零退出。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail=0

tracked_data="$(git ls-files -- 'data' 'data/**' || true)"
if [[ -n "$tracked_data" ]]; then
  echo "拒绝：data/ 已被 git 跟踪。下一步：从索引移除，不要提交 data/。" >&2
  echo "$tracked_data" >&2
  fail=1
fi

tracked_env="$(git ls-files -- '.env' || true)"
if [[ "$tracked_env" == ".env" ]]; then
  echo "拒绝：.env 已被 git 跟踪。" >&2
  fail=1
fi

files=()
while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  case "$file" in
    scripts/check_secrets.sh) continue ;;
  esac
  if [[ -f "$file" ]]; then
    files+=("$file")
  fi
done < <(git diff --cached --name-only --diff-filter=ACMR)

if [[ ${#files[@]} -eq 0 ]]; then
  if [[ "$fail" -ne 0 ]]; then
    exit 1
  fi
  echo "没有暂存文件可扫描。data/ 跟踪检查已完成。"
  exit 0
fi

# 模式在运行时拼接，避免本脚本源码里出现可提交的密钥字面量。
api_key_re='sk-'"[A-Za-z0-9_-]{20,}"
aws_re='AKIA'"[0-9A-Z]{16}"
slack_re='xox'"[baprs]-[0-9A-Za-z-]{10,}"
assign_re='(api[_-]?key|secret|token)["'\'']?[[:space:]]*[:=][[:space:]]*["'\''][A-Za-z0-9_./+\-]{20,}["'\'']'
bearer_re='Bearer '"[A-Za-z0-9._-]{20,}"
cookie_re='Cook''ie:[[:space:]]*[^[:space:]]{8,}'
cookie_re2='(wt2=|__zp_|SESSIONID=)[A-Za-z0-9._%-]{8,}'

scan() {
  local name="$1"
  local regex="$2"
  local output
  output="$(grep -E -n -I -e "$regex" "${files[@]}" || true)"
  if [[ -n "$output" ]]; then
    echo "拒绝：发现 ${name}：" >&2
    echo "$output" >&2
    fail=1
  fi
}

scan "API Key 样式" "$api_key_re"
scan "云厂商访问密钥样式" "$aws_re"
scan "Slack 令牌样式" "$slack_re"
scan "密钥赋值样式" "$assign_re"
scan "Bearer 令牌样式" "$bearer_re"
scan "Cookie 请求头样式" "$cookie_re"
scan "会话 Cookie 样式" "$cookie_re2"

if [[ "$fail" -ne 0 ]]; then
  echo "提交前检查未通过。相关文件未被视为可以安全提交。" >&2
  exit 1
fi

echo "暂存文件未发现 Key、Cookie 样式，data/ 也没有被跟踪。"
exit 0
