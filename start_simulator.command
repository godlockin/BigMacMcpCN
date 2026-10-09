#!/bin/bash
set -e
cd "$(dirname "$0")"
mkdir -p /tmp/Codex-tasks
task_log="/tmp/Codex-tasks/mcd-simulator-$(date +%Y%m%d-%H%M%S).log"
task_pid=""
trap 'if [ -n "$task_pid" ]; then kill "$task_pid" 2>/dev/null || true; fi' EXIT
if [ ! -x .venv/bin/python ] || [ ! -f .venv/.mcd-installed ]; then
  printf '首次启动：准备本机 Python 环境…\n'
  ( python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt ) > "$task_log" 2>&1 &
  task_pid=$!
  if wait "$task_pid"; then
    touch .venv/.mcd-installed
    printf 'OK success\n'
  else
    printf '安装失败，日志：%s\n' "$task_log"
    tail -n 10 "$task_log"
    exit 1
  fi
  task_pid=""
fi
printf '输入本人 Token 后，在浏览器打开 http://127.0.0.1:8789\n关闭此终端将结束本机服务。日志：%s\n' "$task_log"
( .venv/bin/python simulator.py --token-stdin ) > "$task_log" 2>&1 &
task_pid=$!
if wait "$task_pid"; then
  printf 'OK success\n'
else
  printf '服务退出，日志：%s\n' "$task_log"
  tail -n 10 "$task_log"
  exit 1
fi
