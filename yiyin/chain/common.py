"""接龙插件共用：路径、JSON、文件锁、展示名。"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from nonebot.adapters.onebot.v11 import GroupMessageEvent

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KTV_DATA_DIR = PROJECT_ROOT / "data" / "chain" / "ktv"
DINNER_DATA_DIR = PROJECT_ROOT / "data" / "chain" / "dinner"


def display_name(event: GroupMessageEvent) -> str:
    sender = event.sender
    if not sender:
        return str(event.user_id)
    nick = (sender.nickname or "").strip()
    if nick:
        return nick
    card = (sender.card or "").strip()
    if card:
        return card
    return str(event.user_id)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return {**default}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {**default}
        out = {**default, **data}
        return out
    except (json.JSONDecodeError, OSError):
        return {**default}


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@contextmanager
def group_json_lock(data_dir: Path, group_id: int) -> Generator[Path, None, None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / f"{group_id}.json"
    lock_path = data_dir / f"{group_id}.json.lock"
    lockf = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        yield data_path
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()


def format_chain_message(header: str, participants: list[dict[str, str]]) -> str:
    lines = [header]
    for i, p in enumerate(participants, 1):
        lines.append(f"{i}. {p['nickname']}")
    return "\n".join(lines)
