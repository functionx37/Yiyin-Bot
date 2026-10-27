#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${YIYIN_TMUX_SESSION:-yiyin}"
NAPCAT_QQ="${YIYIN_NAPCAT_QQ:-2676330589}"
[[ "$NAPCAT_QQ" =~ ^[0-9]+$ ]] || { echo "错误: QQ 账号必须是数字" >&2; exit 1; }

usage() {
  echo "用法: $0"
  echo "可选环境变量:"
  echo "  YIYIN_TMUX_SESSION  tmux 会话名，默认: yiyin"
}

[[ "${1:-}" =~ ^(-h|--help)$ ]] && { usage; exit 0; }

if systemctl cat yiyin-bot.service >/dev/null 2>&1; then
  sudo -n systemctl stop yiyin.target yiyin-bot.service
  # Also handle an old service instance left from before the tmux migration.
  if systemctl is-active --quiet yiyin-napcat.service; then
    sudo -n systemctl stop yiyin-napcat.service
  fi
fi

if command -v napcat >/dev/null 2>&1; then
  napcat stop "$NAPCAT_QQ"
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "错误: 未找到命令: tmux" >&2
  exit 1
fi

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Bot 和 NapCat 已停止；tmux 会话不存在: $SESSION_NAME"
  exit 0
fi

tmux kill-session -t "$SESSION_NAME"
echo "已停止 Bot、NapCat 和 tmux 会话: $SESSION_NAME"
