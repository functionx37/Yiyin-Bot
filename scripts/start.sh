#!/usr/bin/env bash
# 一键启动 Yiyin Bot（bot.py + napcat），后台运行
# 用法: ./scripts/start.sh
# 停止: ./scripts/stop.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/.yiyin-bot.pids"
LOG_DIR="$PROJECT_ROOT/logs"
NAPCAT_DIR="$PROJECT_ROOT/napcat"

cd "$PROJECT_ROOT"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 检查是否已在运行
if [[ -f "$PID_FILE" ]]; then
  echo "检测到已有进程在运行（PID 文件存在）。"
  echo "如需重启，请先执行: ./scripts/stop.sh"
  exit 1
fi

echo "正在启动 Yiyin Bot..."

# 1. 启动 NoneBot2
echo "  [1/2] 启动 bot.py..."
uv run bot.py >> "$LOG_DIR/bot.log" 2>&1 &
BOT_PID=$!
echo "$BOT_PID" >> "$PID_FILE"
sleep 2  # 等待 bot 先监听端口，再启动 napcat

# 2. 启动 NapCat（需在 napcat 目录下运行以使用本地配置）
echo "  [2/2] 启动 napcat..."
if [[ ! -d "$NAPCAT_DIR" ]]; then
  echo "错误: napcat 目录不存在，请先配置 NapCat。"
  kill $BOT_PID 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
(cd "$NAPCAT_DIR" && napcat) >> "$LOG_DIR/napcat.log" 2>&1 &
NAP_PID=$!
echo "$NAP_PID" >> "$PID_FILE"

echo ""
echo "启动完成！"
echo "  - bot.py PID: $BOT_PID"
echo "  - napcat PID: $NAP_PID"
echo "  - 日志: $LOG_DIR/bot.log, $LOG_DIR/napcat.log"
echo ""
echo "停止机器人: ./scripts/stop.sh"
