"""
字谜/梗图子功能：强强
- 命令：/强强 （文本1）（文本2）（文本3）
- 基于 bibi.jpg 在三个箭头上方绘制文本，直接发送不存储
"""

import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BIBI_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "ziming" / "bibi.jpg"

# ==================== 字体 ====================
_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
]
_font_path_cache: str | None = None


def _get_font_path() -> str | None:
    global _font_path_cache
    if _font_path_cache is not None:
        return _font_path_cache
    for p in _FONT_PATHS:
        if Path(p).exists():
            _font_path_cache = p
            return p
    _font_path_cache = None
    return None


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _get_font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


# ==================== 解析 （文本1）（文本2）（文本3） ====================
def _parse_strong_args(text: str) -> list[str]:
    """解析中文括号内的三组文本，不足补空字符串"""
    # 匹配 （...） 或 （） 即允许空
    parts = re.findall(r"（(.*?)）", text)
    while len(parts) < 3:
        parts.append("")
    return parts[:3]


# ==================== 强强图片生成 ====================
# 三个箭头上方大致比例位置（基于 bibi.jpg 构图：三人像等距，箭头在上方）
_TEXT_X_RATIOS = (1 / 6, 1 / 2, 5 / 6)  # 左、中、右
_TEXT_Y_RATIO = 0.10   # 文字基线约在画面顶部 10% 处（箭头上方）
_FONT_SIZE_RATIO = 1 / 22  # 字号 ≈ 图宽 / 22


def _draw_bibi(texts: list[str]) -> bytes:
    """在 bibi.jpg 上绘制三处文本，返回 PNG 字节"""
    if not BIBI_IMAGE_PATH.exists():
        raise FileNotFoundError(f"模板图片不存在：{BIBI_IMAGE_PATH}")

    img = Image.open(BIBI_IMAGE_PATH).convert("RGB")
    w, h = img.size

    font_size = max(14, min(72, int(w * _FONT_SIZE_RATIO)))
    font = _get_font(font_size)
    draw = ImageDraw.Draw(img)

    # 黑色描边可读性更好，白底用黑色字
    fill = (0, 0, 0)
    outline = (255, 255, 255)
    stroke_width = max(1, font_size // 20)

    for i, s in enumerate(texts):
        if not s:
            continue
        cx = int(w * _TEXT_X_RATIOS[i])
        cy = int(h * _TEXT_Y_RATIO)
        # 使用 getbbox 得到文字框，锚点取水平居中、基线在 cy
        bbox = draw.textbbox((0, 0), s, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = cx - tw // 2
        y = cy - th  # 使文字在 cy 上方
        draw.text(
            (x, y),
            s,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=outline,
        )

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


# ==================== 命令 ====================
qiangqiang_cmd = on_command("强强", priority=10, block=True)


@qiangqiang_cmd.handle()
async def handle_qiangqiang(event: MessageEvent, args: CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await qiangqiang_cmd.finish(
            "用法：/强强 （文本1）（文本2）（文本3）\n"
            "用中文括号括起三段文字，中间可以为空，例如：/强强 （左）（中）（右）"
        )
    parts = _parse_strong_args(raw)
    try:
        out_bytes = _draw_bibi(parts)
    except FileNotFoundError as e:
        await qiangqiang_cmd.finish(str(e))
    except Exception as e:
        await qiangqiang_cmd.finish(f"生成失败：{e}")
    await qiangqiang_cmd.finish(MessageSegment.image(out_bytes))
