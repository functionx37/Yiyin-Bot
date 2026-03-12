"""
梗图子功能：想
- 命令：/想 <文本>
- 基于 motis.jpg 在左上角气泡中填入文本，支持 emoji，自动调整字号与换行
- 文字用 msyh.ttc，emoji 用 SEGUIEMJ.TTF 彩色显示
"""

from io import BytesIO
from pathlib import Path

import emoji
from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOTIS_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "meme" / "motis.jpg"
TEXT_FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "msyh.ttc"
EMOJI_FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "SEGUIEMJ.TTF"

# ==================== 气泡区域（相对图片宽高的比例） ====================
_BUBBLE_LEFT_RATIO = 0.05
_BUBBLE_TOP_RATIO = 0.04
_BUBBLE_WIDTH_RATIO = 0.40
_BUBBLE_HEIGHT_RATIO = 0.22
_BUBBLE_PADDING = 0.03

_FONT_SIZE_MIN = 14
_FONT_SIZE_MAX = 96
_LINE_SPACING = 1.2
_CENTER_OFFSET_X_RATIO = 0.04
_CENTER_OFFSET_Y_RATIO = 0.05

_ZWJ = 0x200D
_VS15 = 0xFE0E
_VS16 = 0xFE0F
_COMBINING_CODEPOINTS = frozenset({_ZWJ, _VS15, _VS16})


def _get_text_font(size: int) -> ImageFont.FreeTypeFont:
    if not TEXT_FONT_PATH.exists():
        raise FileNotFoundError(
            f"未找到字体，请将 msyh.ttc 放到 assets/fonts/ 目录"
        )
    return ImageFont.truetype(str(TEXT_FONT_PATH), size)


def _get_emoji_font(size: int) -> ImageFont.FreeTypeFont:
    if not EMOJI_FONT_PATH.exists():
        raise FileNotFoundError(
            f"未找到字体，请将 SEGUIEMJ.TTF 放到 assets/fonts/ 目录"
        )
    return ImageFont.truetype(str(EMOJI_FONT_PATH), size)


def _get_segments(text: str) -> list[tuple[str, bool]]:
    """将文本按 emoji 切分为 [(片段, 是否emoji), ...]"""
    segments: list[tuple[str, bool]] = []
    emoji_matches = list(emoji.emoji_list(text))
    last_end = 0
    for m in emoji_matches:
        if m["match_start"] > last_end:
            segments.append((text[last_end : m["match_start"]], False))
        segments.append((m["emoji"], True))
        last_end = m["match_end"]
    if last_end < len(text):
        segments.append((text[last_end:], False))
    return segments if segments else [(text, False)]


def _measure_segment(
    seg: str, is_emoji: bool, text_font: ImageFont.FreeTypeFont, emoji_font: ImageFont.FreeTypeFont
) -> float:
    if not seg:
        return 0.0
    f = emoji_font if is_emoji else text_font
    return f.getlength(seg)


def _measure_line(
    line: str,
    text_font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont,
) -> float:
    total = 0.0
    for seg, is_emoji in _get_segments(line):
        total += _measure_segment(seg, is_emoji, text_font, emoji_font)
    return total


def _wrap_text(
    text: str,
    text_font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont,
    max_width: float,
) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        segments = _get_segments(para)
        cur_line = ""
        cur_w = 0.0
        i = 0
        while i < len(segments):
            seg, is_emoji = segments[i]
            if is_emoji:
                seg_w = _measure_segment(seg, True, text_font, emoji_font)
                if cur_w + seg_w > max_width and cur_line:
                    lines.append(cur_line)
                    cur_line = ""
                    cur_w = 0.0
                cur_line += seg
                cur_w += seg_w
                i += 1
            else:
                for ch in seg:
                    cw = text_font.getlength(ch)
                    if cur_w + cw > max_width:
                        if cur_line:
                            lines.append(cur_line)
                            cur_line = ""
                            cur_w = 0.0
                        if cw > max_width:
                            cur_line = ch
                            cur_w = cw
                        else:
                            cur_line += ch
                            cur_w += cw
                    else:
                        cur_line += ch
                        cur_w += cw
                i += 1
        if cur_line:
            lines.append(cur_line)
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
    text_font = _get_text_font(font_size)
    emoji_font = _get_emoji_font(font_size)

    for size in range(_FONT_SIZE_MAX, _FONT_SIZE_MIN - 1, -2):
        text_font = _get_text_font(size)
        emoji_font = _get_emoji_font(size)
        lines = _wrap_text(text, text_font, emoji_font, bubble_w)

        ascent, descent = text_font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        total_h = line_h * len(lines)
        max_line_w = max(
            _measure_line(ln if ln else " ", text_font, emoji_font) for ln in lines
        )

        if total_h <= bubble_h and max_line_w <= bubble_w:
            font_size = size
            break
    else:
        font_size = _FONT_SIZE_MIN
        text_font = _get_text_font(font_size)
        emoji_font = _get_emoji_font(font_size)
        lines = _wrap_text(text, text_font, emoji_font, bubble_w)

    ascent, descent = text_font.getmetrics()
    line_h = int((ascent + descent) * _LINE_SPACING)
    total_h = line_h * len(lines)
    max_line_w = max(
        _measure_line(ln if ln else " ", text_font, emoji_font) for ln in lines
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
            lw = _measure_line(line, text_font, emoji_font)
            lx = text_x + (max_line_w - lw) / 2
            cur_x = lx
            for seg, is_emoji in _get_segments(line):
                if not seg:
                    continue
                if is_emoji:
                    draw.text(
                        (cur_x, text_y),
                        seg,
                        font=emoji_font,
                        embedded_color=True,
                    )
                else:
                    draw.text(
                        (cur_x, text_y),
                        seg,
                        font=text_font,
                        fill=fill,
                        stroke_width=stroke_w,
                        stroke_fill=(255, 255, 255),
                    )
                cur_x += _measure_segment(seg, is_emoji, text_font, emoji_font)
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
