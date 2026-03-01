#!/usr/bin/env bash
# 停止 Yiyin Bot
# 用法: ./scripts/stop.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/.yiyin-bot.pids"

if [[ ! -f "$PID_FILE" ]]; then
  echo "未检测到运行中的进程。"
  exit 0
fi

echo "正在停止 Yiyin Bot..."
while read -r pid; do
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "  已停止 PID $pid"
  fi
done < "$PID_FILE"
rm -f "$PID_FILE"
echo "已全部停止。"
