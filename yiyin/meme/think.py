"""
梗图子功能：想
- 命令：/想 <文本>
- 命令：/想 %<模板名> <文本>
- 命令：/想 list
- 模板来自 assets/images/meme/think；default 使用 0.jpg，其余模板共用一套气泡布局
- 统一使用 msyh.ttc，emoji 由 pilmoji 自动渲染
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFont
from pilmoji import Pilmoji
from pilmoji.helpers import Node, NodeType, to_nodes

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
THINK_DIR = _PROJECT_ROOT / "assets" / "images" / "meme" / "think"
THINK_LIST_PATH = THINK_DIR / "think_list.json"
FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "msyh.ttc"

_FONT_SIZE_MIN = 14
_FONT_SIZE_MAX = 96
_LINE_SPACING = 1.2


@dataclass(frozen=True)
class _TemplateConfig:
    image_path: Path
    bubble_left_ratio: float
    bubble_top_ratio: float
    bubble_width_ratio: float
    bubble_height_ratio: float
    bubble_padding_ratio: float
    center_offset_x_ratio: float
    center_offset_y_ratio: float


_DEFAULT_TEMPLATE = _TemplateConfig(
    image_path=THINK_DIR / "0.jpg",
    bubble_left_ratio=0.05,
    bubble_top_ratio=0.04,
    bubble_width_ratio=0.40,
    bubble_height_ratio=0.22,
    bubble_padding_ratio=0.03,
    center_offset_x_ratio=0.045,
    center_offset_y_ratio=0.08,
)

_COMMON_TEMPLATE = _TemplateConfig(
    image_path=THINK_DIR / "1.jpg",
    bubble_left_ratio=0.075,
    bubble_top_ratio=0.085,
    bubble_width_ratio=0.43,
    bubble_height_ratio=0.215,
    bubble_padding_ratio=0.02,
    center_offset_x_ratio=0.01,
    center_offset_y_ratio=0.065,
)


def _load_template_map() -> dict[str, str]:
    if not THINK_LIST_PATH.exists():
        raise FileNotFoundError(f"模板列表不存在：{THINK_LIST_PATH}")
    with open(THINK_LIST_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("think_list.json 格式错误")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key.strip()] = value.strip()
    return result


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise FileNotFoundError("未找到字体，请将 msyh.ttc 放到 assets/fonts/ 目录")
    return ImageFont.truetype(str(FONT_PATH), size)


def _measure_line_pilmoji(
    line: str,
    font: ImageFont.FreeTypeFont,
    emoji_scale_factor: float = 1.0,
    node_spacing: int = 0,
) -> float:
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
        buf_nodes: list[Node] = []
        cur_w = 0.0
        space_len = font.getlength(" ")
        if space_len <= 0:
            space_len = float(font.size) / 2

        def _node_width(node: Node) -> float:
            if node.type == NodeType.text:
                return font.getlength(node.content)
            w = round(emoji_scale_factor * font.size)
            n_spaces = max(1, round(w / space_len))
            return font.getlength(" " * n_spaces)

        def _flush() -> None:
            if buf_nodes:
                lines.append("".join(n.content for n in buf_nodes))
                buf_nodes.clear()

        for node in line_nodes:
            nw = _node_width(node)
            if node.type == NodeType.text and nw > max_width:
                _flush()
                for ch in node.content:
                    cw = font.getlength(ch)
                    if cw < 1:
                        cw = float(font.size)
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


def _resolve_template(alias: str | None) -> tuple[str, _TemplateConfig]:
    templates = _load_template_map()
    key = (alias or "default").strip() or "default"
    filename = templates.get(key)
    if filename is None:
        raise KeyError(key)
    image_path = THINK_DIR / filename
    if filename == "0.jpg":
        return key, _DEFAULT_TEMPLATE
    return key, _COMMON_TEMPLATE.__class__(
        image_path=image_path,
        bubble_left_ratio=_COMMON_TEMPLATE.bubble_left_ratio,
        bubble_top_ratio=_COMMON_TEMPLATE.bubble_top_ratio,
        bubble_width_ratio=_COMMON_TEMPLATE.bubble_width_ratio,
        bubble_height_ratio=_COMMON_TEMPLATE.bubble_height_ratio,
        bubble_padding_ratio=_COMMON_TEMPLATE.bubble_padding_ratio,
        center_offset_x_ratio=_COMMON_TEMPLATE.center_offset_x_ratio,
        center_offset_y_ratio=_COMMON_TEMPLATE.center_offset_y_ratio,
    )


def _draw_template(text: str, template: _TemplateConfig) -> bytes:
    if not template.image_path.exists():
        raise FileNotFoundError(f"模板图片不存在：{template.image_path}")

    base = Image.open(template.image_path).convert("RGB")
    w, h = base.size

    pad = int(min(w, h) * template.bubble_padding_ratio)
    bubble_left = int(w * template.bubble_left_ratio) + pad
    bubble_top = int(h * template.bubble_top_ratio) + pad
    bubble_w = int(w * template.bubble_width_ratio) - pad * 2
    bubble_h = int(h * template.bubble_height_ratio) - pad * 2

    if not text.strip():
        buf = BytesIO()
        base.save(buf, format="PNG")
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
        max_line_w = max(_measure_line_pilmoji(ln if ln else " ", font) for ln in lines)
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
    max_line_w = max(_measure_line_pilmoji(ln if ln else " ", font) for ln in lines)

    text_x = bubble_left + (bubble_w - max_line_w) / 2 + int(w * template.center_offset_x_ratio)
    text_y = bubble_top + (bubble_h - total_h) / 2 + int(h * template.center_offset_y_ratio)
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


def _build_forward_nodes(bot_name: str, bot_uin: str, items: list[str]) -> list[dict]:
    return [
        {
            "type": "node",
            "data": {
                "name": bot_name,
                "uin": bot_uin,
                "content": MessageSegment.text(item),
            },
        }
        for item in items
    ]


def _parse_xiang_args(raw: str) -> tuple[str | None, str]:
    stripped = raw.strip()
    if stripped == "list":
        return "LIST", ""
    if stripped.startswith("%"):
        alias, _, text = stripped[1:].partition(" ")
        return alias.strip(), text.strip()
    return None, stripped


xiang_cmd = on_command("想", priority=10, block=True)


@xiang_cmd.handle()
async def handle_xiang(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    alias, text = _parse_xiang_args(raw)

    if alias == "LIST":
        if not isinstance(event, GroupMessageEvent):
            await xiang_cmd.finish("list 仅支持群聊使用")
        templates = _load_template_map()
        ordered_names = [name for name in templates.keys() if name != "default"]
        bot_name = "37"
        bot_uin = str(bot.self_id)
        nodes = _build_forward_nodes(bot_name, bot_uin, ordered_names)
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        await xiang_cmd.finish()

    if not text:
        await xiang_cmd.finish(
            "用法：/想 <文本>\n"
            "或：/想 %<模板名> <文本>\n"
            "或：/想 list"
        )

    fallback_notice: str | None = None
    try:
        _, template = _resolve_template(alias)
    except KeyError:
        fallback_notice = f"不存在模板：{alias}"
        _, template = _resolve_template("default")
    except FileNotFoundError as e:
        await xiang_cmd.finish(str(e))

    try:
        out_bytes = _draw_template(text, template)
    except FileNotFoundError as e:
        await xiang_cmd.finish(str(e))
    except Exception as e:
        await xiang_cmd.finish(f"生成失败：{e}")

    if fallback_notice:
        if isinstance(event, GroupMessageEvent):
            templates = _load_template_map()
            ordered_names = [name for name in templates.keys() if name != "default"]
            bot_name = "37"
            bot_uin = str(bot.self_id)
            nodes = _build_forward_nodes(bot_name, bot_uin, ordered_names)
            await bot.send(event, fallback_notice)
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        else:
            await bot.send(event, fallback_notice)

    await bot.send(event, MessageSegment.image(out_bytes))
    await xiang_cmd.finish()
