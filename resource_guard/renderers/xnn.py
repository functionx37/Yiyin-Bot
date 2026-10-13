from __future__ import annotations
"""
梗图子功能：男娘
- 命令：/男娘 [@群友]
- 将 xnn.png 按头像尺寸缩放后叠在头像上（保留 PNG 透明通道）；无有效 @ 时对发送者生效
"""
import asyncio
from io import BytesIO
from pathlib import Path
import httpx
from PIL import Image
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
XNN_IMAGE_PATH = _PROJECT_ROOT / 'assets' / 'images' / 'meme' / 'xnn.png'
_AVATAR_URL = 'http://q1.qlogo.cn/g?b=qq&nk={qq}&s=640'

def _first_at_qq(args: Message) -> int | None:
    """取第一个有效 at 的 qq；@全体成员等无效时返回 None。"""
    for seg in args:
        if seg.type != 'at':
            continue
        raw = seg.data.get('qq')
        if raw in (None, 'all'):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None

def _composite_xnn(avatar_bytes: bytes) -> bytes:
    if not XNN_IMAGE_PATH.exists():
        raise FileNotFoundError(f'模板图片不存在：{XNN_IMAGE_PATH}')
    base = Image.open(BytesIO(avatar_bytes)).convert('RGBA')
    w, h = base.size
    overlay = Image.open(XNN_IMAGE_PATH).convert('RGBA')
    overlay = overlay.resize((w, h), Image.Resampling.LANCZOS)
    base.paste(overlay, (0, 0), overlay)
    overlay.close()
    buf = BytesIO()
    base.save(buf, format='PNG')
    base.close()
    buf.seek(0)
    return buf.getvalue()
