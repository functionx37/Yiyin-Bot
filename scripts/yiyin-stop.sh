#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${YIYIN_TMUX_SESSION:-yiyin}"

usage() {
  echo "用法: $0"
  echo "可选环境变量:"
  echo "  YIYIN_TMUX_SESSION  tmux 会话名，默认: yiyin"
}

[[ "${1:-}" =~ ^(-h|--help)$ ]] && { usage; exit 0; }

if ! command -v tmux >/dev/null 2>&1; then
  echo "错误: 未找到命令: tmux" >&2
  exit 1
fi

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tmux 会话不存在: $SESSION_NAME"
  exit 0
fi

tmux kill-session -t "$SESSION_NAME"
echo "已停止 tmux 会话: $SESSION_NAME"
