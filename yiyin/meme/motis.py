"""
梗图子功能：想
- 命令：/想 <文本>
- 基于 motis.jpg 在左上角气泡中填入文本，支持 emoji，自动调整字号与换行
- 统一使用 msyh.ttc，emoji 由 pilmoji 自动渲染
"""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFont
from pilmoji import Pilmoji
from pilmoji.helpers import Node, NodeType, to_nodes

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOTIS_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "meme" / "motis.jpg"
FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "msyh.ttc"

# ==================== 气泡区域（相对图片宽高的比例） ====================
_BUBBLE_LEFT_RATIO = 0.05
_BUBBLE_TOP_RATIO = 0.04
_BUBBLE_WIDTH_RATIO = 0.40
_BUBBLE_HEIGHT_RATIO = 0.22
_BUBBLE_PADDING = 0.03

_FONT_SIZE_MIN = 14
_FONT_SIZE_MAX = 96
_LINE_SPACING = 1.2
_CENTER_OFFSET_X_RATIO = 0.045
_CENTER_OFFSET_Y_RATIO = 0.08


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise FileNotFoundError(
            f"未找到字体，请将 msyh.ttc 放到 assets/fonts/ 目录"
        )
    return ImageFont.truetype(str(FONT_PATH), size)


def _measure_line_pilmoji(
    line: str,
    font: ImageFont.FreeTypeFont,
    emoji_scale_factor: float = 1.0,
    node_spacing: int = 0,
) -> float:
    """与 Pilmoji 渲染逻辑一致的行宽测量（使用空格占位计算）。"""
    line_nodes = to_nodes(line)
    if not line_nodes:
        return 0.0
    nodes = line_nodes[0]
    space_len = font.getlength(" ")
    if space_len <= 0:
        space_len = float(font.size) / 2
    text_parts: list[str] = []
    for node in nodes:
        if node.type == NodeType.text:
            text_parts.append(node.content)
        else:
            w = round(emoji_scale_factor * font.size)
            size = round(w + node_spacing * 2)
            n_spaces = max(1, round(size / space_len))
            text_parts.append(" " * n_spaces)
    return font.getlength("".join(text_parts))


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    emoji_scale_factor: float = 1.0,
) -> list[str]:
    """按行换行，使用 Pilmoji 风格测量。"""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        parsed = to_nodes(para)
        if not parsed:
            lines.append("")
            continue
        line_nodes = parsed[0]
        buf_nodes: list = []
        cur_w = 0.0
        space_len = font.getlength(" ")
        if space_len <= 0:
            space_len = float(font.size) / 2

        def _node_width(node) -> float:
            if node.type == NodeType.text:
                return font.getlength(node.content)
            w = round(emoji_scale_factor * font.size)
            n_spaces = max(1, round((w + 0) / space_len))
            return font.getlength(" " * n_spaces)

        def _flush():
            if buf_nodes:
                lines.append("".join(n.content for n in buf_nodes))
                buf_nodes.clear()

        for node in line_nodes:
            nw = _node_width(node)
            if node.type == NodeType.text and nw > max_width:
                _flush()
                for ch in node.content:
                    cw = font.getlength(ch) if font.getlength(ch) >= 1 else float(font.size)
                    if cur_w + cw > max_width and buf_nodes:
                        _flush()
                        cur_w = 0.0
                    buf_nodes.append(Node(NodeType.text, ch))
                    cur_w += cw
            elif cur_w + nw > max_width and buf_nodes:
                _flush()
                cur_w = 0.0
                buf_nodes.append(node)
                cur_w = nw
            else:
                buf_nodes.append(node)
                cur_w += nw
        _flush()
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

    for size in range(_FONT_SIZE_MAX, _FONT_SIZE_MIN - 1, -2):
        font = _get_font(size)
        lines = _wrap_text(text, font, bubble_w)

        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * _LINE_SPACING)
        total_h = line_h * len(lines)
        max_line_w = max(
            _measure_line_pilmoji(ln if ln else " ", font) for ln in lines
        )

        if total_h <= bubble_h and max_line_w <= bubble_w:
            font_size = size
            break
    else:
        font_size = _FONT_SIZE_MIN
        font = _get_font(font_size)
        lines = _wrap_text(text, font, bubble_w)

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * _LINE_SPACING)
    total_h = line_h * len(lines)
    max_line_w = max(
        _measure_line_pilmoji(ln if ln else " ", font) for ln in lines
    )

    center_offset_x = int(w * _CENTER_OFFSET_X_RATIO)
    center_offset_y = int(h * _CENTER_OFFSET_Y_RATIO)
    text_x = bubble_left + (bubble_w - max_line_w) / 2 + center_offset_x
    text_y = bubble_top + (bubble_h - total_h) / 2 + center_offset_y

    fill = (0, 0, 0)
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
                    fill=fill,
                    stroke_width=stroke_w,
                    stroke_fill=(255, 255, 255),
                    anchor="lt",
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
