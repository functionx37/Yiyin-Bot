"""
OneBot 图片路径工具：支持本地文件路径，避免将图片加载到内存。

- Docker：nonebot 与 napcat 均挂载 ./data:/app/data，使用 /app/data/... 路径，由 NapCat 读取
- nb run + 原生 NapCat：使用绝对路径（file:///...）
"""

import os
from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"


def _image_source_for_onebot(filepath: Path) -> str | Path:
    """
    返回适合传给 MessageSegment.image() 的路径，供 OneBot 实现（NapCat）读取。

    - Docker：环境变量 ONEBOT_IMAGE_BASE_PATH 由 docker-compose 注入，返回 /app/data/...
    - nb run + 原生 NapCat：返回 Path，转为 file:// 绝对路径
    """
    if not filepath.exists():
        raise FileNotFoundError(f"图片不存在: {filepath}")

    base = os.environ.get("ONEBOT_IMAGE_BASE_PATH")
    if base:
        try:
            rel = filepath.resolve().relative_to(_DATA_DIR.resolve())
        except ValueError:
            return filepath
        return str(Path(base) / rel)

    return filepath


def image_segment_from_path(filepath: Path) -> MessageSegment:
    """从本地文件路径生成图片消息段，不加载到内存。"""
    src = _image_source_for_onebot(filepath)
    return MessageSegment.image(src)
