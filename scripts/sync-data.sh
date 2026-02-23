#!/usr/bin/env bash
# 在多台机器之间同步 data/ 目录
# 用法:
#   指定远程: ./scripts/sync-data.sh push user@server:/path/to/Yiyin-Bot
#   使用默认: ./scripts/sync-data.sh push   （需先配置默认远程，见下方）
#
# 默认远程: 命令行第二参数，或 .env.prod 中的 DATA_SYNC_REMOTE（或环境变量）

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_ROOT/data"
ENV_PROD="$PROJECT_ROOT/.env.prod"

if [[ ! -d "$DATA_DIR" ]]; then
  mkdir -p "$DATA_DIR"
fi

# 从 .env.prod 读取 DATA_SYNC_REMOTE（若尚未设置）
load_env_prod_remote() {
  [[ -n "$DATA_SYNC_REMOTE" ]] && return
  [[ ! -f "$ENV_PROD" ]] && return
  local line
  line="$(grep -E '^DATA_SYNC_REMOTE=' "$ENV_PROD" 2>/dev/null | head -n1)"
  if [[ -n "$line" ]]; then
    DATA_SYNC_REMOTE="${line#DATA_SYNC_REMOTE=}"
    DATA_SYNC_REMOTE="${DATA_SYNC_REMOTE#\"}"
    DATA_SYNC_REMOTE="${DATA_SYNC_REMOTE%\"}"
    DATA_SYNC_REMOTE="$(echo "$DATA_SYNC_REMOTE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi
}

get_remote() {
  if [[ -n "$2" ]]; then
    echo "$2"
    return
  fi
  load_env_prod_remote
  if [[ -n "$DATA_SYNC_REMOTE" ]]; then
    echo "$DATA_SYNC_REMOTE"
    return
  fi
  echo ""
}

usage() {
  echo "用法: $0 <push|pull|diff> [user@host:path]"
  echo "  若省略第二参数，则使用 .env.prod 中的 DATA_SYNC_REMOTE（或环境变量）"
  echo "  例: $0 push user@myserver:/home/me/Yiyin-Bot"
  echo "  例: $0 pull   # 使用默认远程"
  exit 1
}

[[ $# -lt 1 ]] && usage
MODE="$1"
REMOTE="$(get_remote "$MODE" "$2")"
[[ -z "$REMOTE" ]] && { echo "错误: 未指定远程。请传入 user@host:path 或配置默认远程。" >&2; usage; }

case "$MODE" in
  push)
    echo "正在将 data/ 同步到 $REMOTE ..."
    rsync -avz --delete "$DATA_DIR/" "$REMOTE/data/"
    ;;
  pull)
    echo "正在从 $REMOTE 拉取 data/ ..."
    rsync -avz --delete "$REMOTE/data/" "$DATA_DIR/"
    ;;
  diff)
    echo "本地 -> 远程 差异预览 (不会实际传输):"
    rsync -avzn --delete "$DATA_DIR/" "$REMOTE/data/"
    ;;
  *)
    usage
    ;;
esac

echo "完成."
