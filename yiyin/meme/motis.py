"""
梗图子功能：想
- 命令：/想 <文本>
- 基于 motis.jpg 在左上角气泡中填入文本，支持 emoji，自动调整字号与换行
"""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOTIS_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "meme" / "motis.jpg"
FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "SEGUIEMJ.TTF"

# ==================== 气泡区域（相对图片宽高的比例） ====================
_BUBBLE_LEFT_RATIO = 0.05
_BUBBLE_TOP_RATIO = 0.04
_BUBBLE_WIDTH_RATIO = 0.40
_BUBBLE_HEIGHT_RATIO = 0.22
_BUBBLE_PADDING = 0.03

_FONT_SIZE_MIN = 14
_FONT_SIZE_MAX = 96
_LINE_SPACING = 1.2
# 文字中心相对气泡几何中心的偏移（右移、下移）
_CENTER_OFFSET_X_RATIO = 0.04
_CENTER_OFFSET_Y_RATIO = 0.05

_ZWJ = 0x200D
_VS15 = 0xFE0E
_VS16 = 0xFE0F
_COMBINING_CODEPOINTS = frozenset({_ZWJ, _VS15, _VS16})


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise FileNotFoundError(
            f"未找到字体，请将 SEGUIEMJ.TTF 放到 assets/fonts/ 目录"
        )
    return ImageFont.truetype(str(FONT_PATH), size)


def _char_width(ch: str, font: ImageFont.FreeTypeFont, emoji_w: float) -> float:
    cp = ord(ch)
    if cp in _COMBINING_CODEPOINTS or 0xE0020 <= cp <= 0xE007F:
        return 0
    w = font.getlength(ch)
    if w < 1 and cp > 255:
        return emoji_w
    return w


def _measure_line(text: str, font: ImageFont.FreeTypeFont, emoji_w: float) -> float:
    return sum(_char_width(c, font, emoji_w) for c in text)


def _wrap_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: float, emoji_w: float
) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        buf = ""
        cur_w = 0.0
        for ch in para:
            cw = _char_width(ch, font, emoji_w)
            if cur_w + cw > max_width:
                if buf:
                    lines.append(buf)
                buf = ch
                cur_w = cw
            else:
                buf += ch
                cur_w += cw
        if buf:
            lines.append(buf)
    return lines or [""]


def _draw_motis(text: str) -> bytes:
    if not MOTIS_IMAGE_PATH.exists():
        raise FileNotFoundError(f"模板图片不存在：{MOTIS_IMAGE_PATH}")

    base = Image.open(MOTIS_IMAGE_PATH).convert("RGB")
    w, h = base.size

    pad = int(min(w, h) * _BUBBLE_PADDING)
    bubble_left = int(w * _BUBBLE_LEFT_RATIO) + pad
    bubble_top = int(h * _BUBBLE_TOP_RATIO) + pad
    bubble_w = int(w * _BUBBLE_WIDTH_RATIO) - pad * 2
    bubble_h = int(h * _BUBBLE_HEIGHT_RATIO) - pad * 2

    if not text.strip():
        buf = BytesIO()
        base.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()

    font_size = _FONT_SIZE_MAX
    lines: list[str] = []
    font = _get_font(font_size)
    emoji_w = float(font_size)

    for size in range(_FONT_SIZE_MAX, _FONT_SIZE_MIN - 1, -2):
        font = _get_font(size)
        emoji_w = float(size)
        lines = _wrap_text(text, font, bubble_w, emoji_w)

        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        total_h = line_h * len(lines)
        max_line_w = max(
            _measure_line(ln if ln else " ", font, emoji_w) for ln in lines
        )

        if total_h <= bubble_h and max_line_w <= bubble_w:
            font_size = size
            break
    else:
        font_size = _FONT_SIZE_MIN
        font = _get_font(font_size)
        emoji_w = float(font_size)
        lines = _wrap_text(text, font, bubble_w, emoji_w)

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * _LINE_SPACING)
    total_h = line_h * len(lines)
    max_line_w = max(
        _measure_line(ln if ln else " ", font, emoji_w) for ln in lines
    )

    center_offset_x = int(w * _CENTER_OFFSET_X_RATIO)
    center_offset_y = int(h * _CENTER_OFFSET_Y_RATIO)
    text_x = bubble_left + (bubble_w - max_line_w) / 2 + center_offset_x
    text_y = bubble_top + (bubble_h - total_h) / 2 + center_offset_y

    draw = ImageDraw.Draw(base)
    fill = (0, 0, 0)
    stroke_w = max(1, font_size // 24)

    for line in lines:
        if line:
            lw = _measure_line(line, font, emoji_w)
            lx = text_x + (max_line_w - lw) / 2
            draw.text(
                (lx, text_y),
                line,
                font=font,
                fill=fill,
                stroke_width=stroke_w,
                stroke_fill=(255, 255, 255),
            )
        text_y += line_h

    buf = BytesIO()
    base.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


xiang_cmd = on_command("想", priority=10, block=True)


@xiang_cmd.handle()
async def handle_xiang(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await xiang_cmd.finish(
            "用法：/想 <文本>\n"
            "在 motis 左上角气泡中填入文字，支持 emoji，自动调整字号与换行"
        )
    try:
        out_bytes = _draw_motis(raw)
    except FileNotFoundError as e:
        await xiang_cmd.finish(str(e))
    except Exception as e:
        await xiang_cmd.finish(f"生成失败：{e}")
    await bot.send(event, MessageSegment.image(out_bytes))
    await xiang_cmd.finish()
