"""
聊天截图生成模块 — 使用 Pillow + pilmoji 绘制模拟 QQ 群聊风格的单条消息截图
pilmoji 自动将 emoji 渲染为图片叠加到画布上，解决 CJK 字体缺少 emoji 字形的问题。
"""

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

_FONT_SEARCH_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
]

_font_path_cache: str | None = ...  # type: ignore[assignment]


def _resolve_font_path() -> str | None:
    global _font_path_cache
    if _font_path_cache is not ...:
        return _font_path_cache
    for p in _FONT_SEARCH_PATHS:
        if Path(p).exists():
            _font_path_cache = p
            return p
    _font_path_cache = None
    return None


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _resolve_font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size)).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


_ZWJ = 0x200D
_VS15 = 0xFE0E
_VS16 = 0xFE0F
_COMBINING_CODEPOINTS = frozenset({_ZWJ, _VS15, _VS16})


def _char_width(ch: str, font: ImageFont.FreeTypeFont, emoji_w: float) -> float:
    """获取单个字符的渲染宽度。
    对 ZWJ / 变体选择符返回 0；对字体中缺失的非 ASCII 字符按 emoji 宽度估算。"""
    cp = ord(ch)
    if cp in _COMBINING_CODEPOINTS or 0xE0020 <= cp <= 0xE007F:
        return 0
    w = font.getlength(ch)
    if w < 1 and cp > 255:
        return emoji_w
    return w


def _measure_line(text: str, font: ImageFont.FreeTypeFont, emoji_w: float) -> float:
    return sum(_char_width(ch, font, emoji_w) for ch in text)


def _wrap_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, emoji_w: float
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        buf = ""
        current_w = 0.0
        for ch in paragraph:
            ch_w = _char_width(ch, font, emoji_w)
            if current_w + ch_w > max_width:
                if buf:
                    lines.append(buf)
                buf = ch
                current_w = ch_w
            else:
                buf += ch
                current_w += ch_w
        if buf:
            lines.append(buf)
    return lines or [""]


_CANVAS_W = 900
_BG_COLOR = (241, 241, 241)
_BUBBLE_COLOR = (255, 255, 255)
_NICK_COLOR = (149, 149, 149)
_TEXT_COLOR = (17, 17, 17)
_AVATAR_SIZE = 85
_AVATAR_X = 50
_MSG_X = 155
_BUBBLE_PH = 22
_BUBBLE_PV = 18
_BUBBLE_R = 15
_NICK_FONT_SIZE = 22
_TEXT_FONT_SIZE = 32
_LINE_GAP = 8
_MAX_TEXT_W = 600
_TOP_PAD = 30
_BOT_PAD = 30


def generate_chat_screenshot(
    avatar_bytes: bytes,
    nickname: str,
    text: str,
) -> bytes:
    nick_font = _get_font(_NICK_FONT_SIZE)
    text_font = _get_font(_TEXT_FONT_SIZE)
    emoji_w = float(_TEXT_FONT_SIZE)

    wrapped = _wrap_text(text, text_font, _MAX_TEXT_W, emoji_w)

    ascent, descent = text_font.getmetrics()
    line_h = ascent + descent
    max_line_w = max(_measure_line(ln if ln else " ", text_font, emoji_w) for ln in wrapped)
    text_block_h = line_h * len(wrapped) + _LINE_GAP * max(len(wrapped) - 1, 0)

    nick_asc, nick_desc = nick_font.getmetrics()
    nick_h = nick_asc + nick_desc

    bub_w = int(max_line_w) + _BUBBLE_PH * 2
    bub_h = text_block_h + _BUBBLE_PV * 2

    gap_nb = 10
    total_h = _TOP_PAD + nick_h + gap_nb + bub_h + _BOT_PAD
    total_h = max(total_h, _AVATAR_SIZE + _TOP_PAD + _BOT_PAD)

    canvas = Image.new("RGB", (_CANVAS_W, total_h), _BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    # Avatar
    try:
        ava = Image.open(BytesIO(avatar_bytes))
    except Exception:
        ava = Image.new("RGB", (_AVATAR_SIZE, _AVATAR_SIZE), (200, 200, 200))
    circle_ava = _circle_crop(ava, _AVATAR_SIZE)
    canvas.paste(circle_ava, (_AVATAR_X, _TOP_PAD), circle_ava)

    # Bubble
    bub_y = _TOP_PAD + nick_h + gap_nb
    draw.rounded_rectangle(
        (_MSG_X, bub_y, _MSG_X + bub_w, bub_y + bub_h),
        radius=_BUBBLE_R,
        fill=_BUBBLE_COLOR,
    )

    # Bubble pointer triangle
    tri_y = bub_y + 15
    draw.polygon(
        [(_MSG_X, tri_y), (_MSG_X - 10, tri_y + 8), (_MSG_X, tri_y + 16)],
        fill=_BUBBLE_COLOR,
    )

    # Nickname & text via Pilmoji (handles emoji rendering)
    with Pilmoji(canvas) as pmoji:
        pmoji.text((_MSG_X, _TOP_PAD), nickname, font=nick_font, fill=_NICK_COLOR)

        tx = _MSG_X + _BUBBLE_PH
        ty = bub_y + _BUBBLE_PV
        for line in wrapped:
            if line:
                pmoji.text((tx, ty), line, font=text_font, fill=_TEXT_COLOR)
            ty += line_h + _LINE_GAP

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


async def async_generate_chat_screenshot(
    avatar_bytes: bytes,
    nickname: str,
    text: str,
) -> bytes:
    return await asyncio.to_thread(
        generate_chat_screenshot, avatar_bytes, nickname, text
    )
