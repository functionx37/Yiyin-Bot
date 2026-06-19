"""
梗图子功能：盐巴
- 命令：/盐巴 <文本>
- 基于 salt.jpg 在左上角对白框中居中填入文字，支持 emoji，自动调整字号与换行
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from pilmoji import Pilmoji

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from yiyin.meme.think import (
    _FONT_SIZE_MAX,
    _FONT_SIZE_MIN,
    _IGNORABLE_EDGE_CHARS,
    _LINE_SPACING,
    _get_font,
    _measure_line_pilmoji,
    _wrap_text,
)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SALT_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "meme" / "salt.jpg"

# 左上角对白框的安全矩形区域，按模板原图 1000x1000 比例定义。
_TEXT_LEFT_RATIO = 0.05
_TEXT_TOP_RATIO = 0.06
_TEXT_WIDTH_RATIO = 0.34
_TEXT_HEIGHT_RATIO = 0.34
_CENTER_OFFSET_X_RATIO = 0.0
_CENTER_OFFSET_Y_RATIO = 0.0


def _normalize_salt_text(text: str) -> str:
    cleaned = text
    while True:
        updated = cleaned.strip().strip(_IGNORABLE_EDGE_CHARS)
        if updated == cleaned:
            return cleaned
        cleaned = updated


def _draw_salt(text: str) -> bytes:
    if not SALT_IMAGE_PATH.exists():
        raise FileNotFoundError(f"模板图片不存在：{SALT_IMAGE_PATH}")

    base = Image.open(SALT_IMAGE_PATH).convert("RGB")
    w, h = base.size

    box_left = int(w * _TEXT_LEFT_RATIO)
    box_top = int(h * _TEXT_TOP_RATIO)
    box_w = int(w * _TEXT_WIDTH_RATIO)
    box_h = int(h * _TEXT_HEIGHT_RATIO)

    if not text.strip():
        buf = BytesIO()
        base.save(buf, format="PNG")
        return buf.getvalue()

    font_size = _FONT_SIZE_MAX
    lines: list[str] = []
    font = _get_font(font_size)

    for size in range(_FONT_SIZE_MAX, _FONT_SIZE_MIN - 1, -2):
        font = _get_font(size)
        lines = _wrap_text(text, font, box_w)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        total_h = line_h * len(lines)
        max_line_w = max(_measure_line_pilmoji(ln if ln else " ", font) for ln in lines)
        if total_h <= box_h and max_line_w <= box_w:
            font_size = size
            break
    else:
        font_size = _FONT_SIZE_MIN
        font = _get_font(font_size)
        lines = _wrap_text(text, font, box_w)

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * _LINE_SPACING)
    total_h = line_h * len(lines)
    max_line_w = max(_measure_line_pilmoji(ln if ln else " ", font) for ln in lines)

    text_x = box_left + (box_w - max_line_w) / 2 + int(w * _CENTER_OFFSET_X_RATIO)
    text_y = box_top + (box_h - total_h) / 2 + int(h * _CENTER_OFFSET_Y_RATIO)
    stroke_w = max(1, font_size // 24)

    with Pilmoji(base) as pmoji:
        for line in lines:
            if line:
                lw = _measure_line_pilmoji(line, font)
                lx = text_x + (max_line_w - lw) / 2
                pmoji.text(
                    (lx, text_y),
                    line,
                    font=font,
                    fill=(0, 0, 0),
                    stroke_width=stroke_w,
                    stroke_fill=(255, 255, 255),
                    anchor="lt",
                )
            text_y += line_h

    buf = BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


salt_cmd = on_command("盐巴", priority=10, block=True)


@salt_cmd.handle()
async def handle_salt(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = _normalize_salt_text(args.extract_plain_text())
    if not text:
        await salt_cmd.finish("用法：/盐巴 <文本>")

    try:
        out_bytes = _draw_salt(text)
    except FileNotFoundError as e:
        await salt_cmd.finish(str(e))
    except Exception as e:
        await salt_cmd.finish(f"生成失败：{e}")

    await bot.send(event, MessageSegment.image(out_bytes))
    await salt_cmd.finish()
