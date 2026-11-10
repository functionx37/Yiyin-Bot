#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BOT_DIR="${BOT_DIR:-$PROJECT_ROOT}"
NAPCAT_DIR="${NAPCAT_DIR:-$PROJECT_ROOT/napcat}"
SESSION_NAME="${YIYIN_TMUX_SESSION:-yiyin}"

usage() {
  echo "用法: $0"
  echo "可选环境变量:"
  echo "  YIYIN_TMUX_SESSION  tmux 会话名，默认: yiyin"
  echo "  BOT_DIR             机器人目录，默认: $PROJECT_ROOT"
  echo "  NAPCAT_DIR          NapCat 目录，默认: $PROJECT_ROOT/napcat"
}

[[ "${1:-}" =~ ^(-h|--help)$ ]] && { usage; exit 0; }

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "错误: 未找到命令: $cmd" >&2
    exit 1
  fi
}

has_local_changes() {
  ! git -C "$BOT_DIR" diff --quiet --ignore-submodules -- || \
    ! git -C "$BOT_DIR" diff --cached --quiet --ignore-submodules -- || \
    [[ -n "$(git -C "$BOT_DIR" ls-files --others --exclude-standard)" ]]
}

restore_stash() {
  if ! git -C "$BOT_DIR" stash pop; then
    echo "错误: git stash pop 时发生冲突，请手动解决后再重新运行 yiyin run。" >&2
    echo "你可以先查看冲突文件，处理完成后再继续。" >&2
    return 1
  fi
}

check_and_update_repo() {
  local upstream local_rev remote_rev base_rev answer stash_created=0 stash_name

  if ! command -v git >/dev/null 2>&1; then
    echo "警告: 未找到 git，跳过远端更新检查。"
    return 0
  fi

  if ! git -C "$BOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  if ! upstream="$(git -C "$BOT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
    echo "未配置 Git 上游分支，跳过远端更新检查。"
    return 0
  fi

  echo "正在检查远端更新..."
  if ! git -C "$BOT_DIR" fetch --quiet; then
    echo "错误: git fetch 失败，请检查网络或远端配置后重试。" >&2
    return 1
  fi

  local_rev="$(git -C "$BOT_DIR" rev-parse HEAD)"
  remote_rev="$(git -C "$BOT_DIR" rev-parse "$upstream")"
  base_rev="$(git -C "$BOT_DIR" merge-base HEAD "$upstream")"

  if [[ "$local_rev" == "$remote_rev" ]] || [[ "$remote_rev" == "$base_rev" ]]; then
    return 0
  fi

  echo "检测到远端分支有更新: $upstream"
  read -r -p "是否先更新代码后再启动？[y/N] " answer
  case "$answer" in
    y|Y|yes|YES)
      ;;
    *)
      echo "已跳过更新，继续启动。"
      return 0
      ;;
  esac

  if has_local_changes; then
    stash_name="yiyin-auto-stash-$(date +%s)"
    echo "检测到本地有未提交修改，正在执行 git stash ..."
    if ! git -C "$BOT_DIR" stash push --include-untracked -m "$stash_name"; then
      echo "错误: git stash 失败，请先手动处理本地修改后再重试。" >&2
      return 1
    fi
    stash_created=1
  fi

  echo "正在执行 git pull --rebase ..."
  if ! git -C "$BOT_DIR" pull --rebase; then
    echo "错误: git pull --rebase 失败，正在放弃本次更新。" >&2
    git -C "$BOT_DIR" rebase --abort >/dev/null 2>&1 || true
    git -C "$BOT_DIR" merge --abort >/dev/null 2>&1 || true
    if (( stash_created )); then
      echo "正在恢复之前暂存的本地修改..."
      restore_stash || true
    fi
    echo "请手动执行 git pull 并解决冲突或其他错误后，再重新运行 yiyin run。" >&2
    return 1
  fi

  if (( stash_created )); then
    echo "正在恢复之前暂存的本地修改..."
    if ! restore_stash; then
      return 1
    fi
  fi

  echo "代码更新完成。"
}

require_command tmux
require_command uv
require_command napcat

if [[ ! -d "$BOT_DIR" ]]; then
  echo "错误: 机器人目录不存在: $BOT_DIR" >&2
  exit 1
fi

mkdir -p "$NAPCAT_DIR"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tmux 会话已存在: $SESSION_NAME"
  exec tmux attach-session -t "$SESSION_NAME"
fi

check_and_update_repo

printf -v bot_cmd 'cd %q && uv run bot.py' "$BOT_DIR"
printf -v napcat_cmd 'cd %q && napcat' "$NAPCAT_DIR"

tmux new-session -d -s "$SESSION_NAME" -n main "$bot_cmd"
tmux split-window -h -t "$SESSION_NAME:main" "$napcat_cmd"
tmux select-layout -t "$SESSION_NAME:main" even-horizontal
tmux select-pane -t "$SESSION_NAME:main.1"

echo "已启动 tmux 会话: $SESSION_NAME"
echo "  左侧 bot    -> $BOT_DIR"
echo "  右侧 napcat -> $NAPCAT_DIR"
echo "正在进入 tmux，会话退出请按 Ctrl+B D"

exec tmux attach-session -t "$SESSION_NAME"
