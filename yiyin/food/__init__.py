"""
NoneBot2 食物图鉴插件（按群隔离）
- 命令：/收集食物 (名字) (#夯/拉/NPC/人上人) (%（标签1）（标签2）) [图片]
- 命令：/收集隐藏食物 (名字) (#夯/拉/NPC/人上人) (%（标签1）（标签2）) [图片]
- 子模块 auto_collect：自动食物收集（常关，需 /启用 自动食物收集）
- 命令：/删除食物 <id/名字> — 仅超级管理员，支持引用食物消息自动提取 ID
- 命令：/补充名字 <id/名字> <新名字> — 支持引用食物消息后 /补充名字 新名字 自动提取 ID
- 命令：/标记 <id/名字> <tag> — 支持引用食物消息后 /标记 标签 自动提取 ID
- 命令：引用食物消息后发送 % <名字> -r <夯/拉/NPC/人上人> -t （标签1）（标签2）
- 命令：/吃 <id/名字/tag/rank> — 支持引用食物消息自动提取 ID
- 命令：/隐藏 <id> — 将普通食物设为隐藏食物，支持引用食物消息自动提取 ID
- 命令：/吃大餐 [数量] — 默认三道菜，最多十道
- 触发：有人发『吃什么』时回复『是啊，吃什么』并随机一张图请你吃（单抽有概率触发隐藏食物）
"""

import asyncio
import json
import random
import re
import string
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from nonebot import on_command, on_keyword, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from yiyin.food.llm_recognition import (
    recognize_food_from_image_bytes,
    suggest_food_name_from_image_bytes,
)
from yiyin.image_utils import maybe_compress_large_png

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "food"

RANK_ALIASES = {
    "夯": "夯",
    "夯爆了": "夯",
    "人上人": "人上人",
    "npc": "NPC",
    "NPC": "NPC",
    "拉": "拉",
    "拉完了": "拉",
}
VALID_RANKS = {"夯", "人上人", "NPC", "拉"}
MAX_EAT_CANDIDATES = 8
EAT_SCORE_THRESHOLD = 0.42
MAX_FEAST_COUNT = 10
_COLLECT_COMMAND_RE = re.compile(r"[/.\!！](?P<command>收集隐藏食物|收集食物)(?=\s|$)")
_COLLECT_RANK_RE = re.compile(r"(?:^|\s)#(夯爆了|夯|拉完了|拉|NPC|npc|人上人)(?=\s|$)")
_COLLECT_TAGS_RE = re.compile(r"%(（[^）]+）(?:（[^）]+）)*)")
_TAG_PARENS_RE = re.compile(r"（([^）]+)）")
_REPLY_EDIT_RE = re.compile(
    r"^\s*%(?P<body>.*?)(?:\s+-r\s+(?P<rank>\S+))?(?:\s+-t\s+(?P<tags>（[^）]+）(?:（[^）]+）)*))?\s*$"
)
_FOOD_ID_PREFIX_RE = re.compile(
    r"食物ID[：:]\s*([A-Za-z0-9]+(?:\s*[、]\s*[A-Za-z0-9]+)*)"
)
_FOOD_LABEL_RE = re.compile(r"『[^』]*』[（(]([A-Za-z0-9]+)[）)]")


def _get_group_dir(group_id: str) -> Path:
    return DATA_DIR / group_id


def _get_index_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "index.json"


def _get_hidden_index_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_index.json"


def _get_images_dir(group_id: str) -> Path:
    return _get_group_dir(group_id) / "images"


def _get_hidden_images_dir(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_images"


def _get_hidden_prob_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_prob.json"


def _get_labels_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "label.json"


def _get_hidden_owners_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_owners.json"


def _get_hidden_up_state_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_up_state.json"


def _load_hidden_prob(group_id: str) -> int:
    f = _get_hidden_prob_file(group_id)
    if not f.exists():
        return 3
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return max(1, min(100, int(data.get("prob", 3))))
    except Exception:
        return 3


def _save_hidden_prob(group_id: str, prob: int) -> None:
    f = _get_hidden_prob_file(group_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump({"prob": prob}, fp, ensure_ascii=False)


def _load_hidden_owners(group_id: str) -> dict[str, list[str]]:
    path = _get_hidden_owners_file(group_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.exception("读取隐藏食物 owner 配置失败")
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for owner, aliases in raw.items():
        if not isinstance(owner, str):
            continue
        owner_name = owner.strip()
        if not owner_name:
            continue
        alias_list: list[str] = []
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    alias_list.append(alias.strip())
        result[owner_name] = alias_list
    return result


def _load_hidden_up_state(group_id: str) -> dict[str, object]:
    path = _get_hidden_up_state_file(group_id)
    if not path.exists():
        return {"up": "", "guaranteed": False}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.exception("读取隐藏食物 up 状态失败")
        return {"up": "", "guaranteed": False}
    if not isinstance(raw, dict):
        return {"up": "", "guaranteed": False}
    up = raw.get("up")
    guaranteed = raw.get("guaranteed")
    return {
        "up": up.strip() if isinstance(up, str) else "",
        "guaranteed": bool(guaranteed),
    }


def _save_hidden_up_state(group_id: str, state: dict[str, object]) -> None:
    path = _get_hidden_up_state_file(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "up": str(state.get("up") or "").strip(),
        "guaranteed": bool(state.get("guaranteed")),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _generate_short_id(existing_ids: set[str]) -> str:
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str, *, hidden: bool = False) -> dict[str, dict]:
    index_file = _get_hidden_index_file(group_id) if hidden else _get_index_file(group_id)
    if not index_file.exists():
        return {}
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("读取{}食物索引失败", "隐藏" if hidden else "")
        return {}


def _save_index(group_id: str, index: dict[str, dict], *, hidden: bool = False) -> None:
    index_file = _get_hidden_index_file(group_id) if hidden else _get_index_file(group_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _load_all_indexes(group_id: str) -> dict[str, dict]:
    normal_index = _load_index(group_id)
    hidden_index = _load_index(group_id, hidden=True)
    merged = dict(normal_index)
    for food_id, entry in hidden_index.items():
        if food_id in merged:
            logger.warning("食物ID同时存在于普通和隐藏索引中：group_id={} food_id={}", group_id, food_id)
        merged[food_id] = entry
    return merged


def _get_food_image_path(
    group_id: str,
    food_id: str,
    entry: dict,
    *,
    hidden: bool = False,
) -> Path:
    filename = entry.get("filename") or f"{food_id}.png"
    images_dir = _get_hidden_images_dir(group_id) if hidden else _get_images_dir(group_id)
    return images_dir / filename


def _fit_image_within(
    image: Image.Image,
    *,
    max_width: int,
    max_height: int,
    max_scale: float = 1.15,
) -> Image.Image:
    width_ratio = max_width / max(1, image.width)
    height_ratio = max_height / max(1, image.height)
    scale = min(width_ratio, height_ratio, max_scale)
    if scale <= 0:
        scale = 1.0
    target_w = max(1, int(round(image.width * scale)))
    target_h = max(1, int(round(image.height * scale)))
    if target_w == image.width and target_h == image.height:
        return image.copy()
    return image.resize((target_w, target_h), Image.Resampling.LANCZOS)


def _create_rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255
    )
    return mask


def _create_feast_background(width: int, height: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (245, 239, 230, 255))
    draw = ImageDraw.Draw(canvas)
    top = (250, 246, 240)
    bottom = (233, 224, 212)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color + (255,))

    accents = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accents)
    accent_draw.ellipse((-140, -120, 420, 380), fill=(255, 255, 255, 78))
    accent_draw.ellipse((width - 440, -100, width + 140, 440), fill=(222, 203, 177, 78))
    accent_draw.ellipse((width - 240, height - 360, width + 120, height + 60), fill=(246, 237, 222, 68))
    accent_draw.ellipse((-180, height - 420, 260, height + 40), fill=(255, 249, 241, 62))
    accents = accents.filter(ImageFilter.GaussianBlur(56))
    canvas.alpha_composite(accents)
    return canvas


def _render_feast_collage(
    group_id: str,
    food_ids: list[str],
    index: dict[str, dict],
) -> bytes | None:
    if not food_ids:
        return None

    count = len(food_ids)
    if count == 1:
        column_count = 1
        canvas_width = 960
        max_image_height = 780
    elif count <= 4:
        column_count = 2
        canvas_width = 1080
        max_image_height = 620
    else:
        column_count = 3
        canvas_width = 1280
        max_image_height = 470

    outer_padding = 52
    top_padding = 52
    bottom_padding = 52
    gap = 28
    card_padding = 18
    card_radius = 34
    image_radius = 28
    content_width = canvas_width - outer_padding * 2
    card_width = (content_width - gap * (column_count - 1)) // column_count
    max_image_width = max(1, card_width - card_padding * 2)

    cards: list[dict[str, object]] = []
    for food_id in food_ids:
        entry = index.get(food_id)
        if not isinstance(entry, dict):
            continue
        image_path = _get_food_image_path(group_id, food_id, entry, hidden=False)
        if not image_path.exists():
            logger.warning("吃大餐拼图缺少图片: group_id={} food_id={}", group_id, food_id)
            continue
        try:
            with Image.open(image_path) as raw:
                raw.load()
                source = ImageOps.exif_transpose(raw).convert("RGB")
        except Exception:
            logger.exception("读取吃大餐拼图图片失败: group_id={} food_id={}", group_id, food_id)
            continue

        fitted = _fit_image_within(
            source,
            max_width=max_image_width,
            max_height=max_image_height,
        )
        card_height = fitted.height + card_padding * 2
        cards.append(
            {
                "image": fitted,
                "card_height": card_height,
            }
        )

    if not cards:
        return None

    placements: list[dict[str, object]] = []
    column_heights = [0] * column_count
    for card in cards:
        column = min(range(column_count), key=lambda idx: column_heights[idx])
        x = outer_padding + column * (card_width + gap)
        y = top_padding + column_heights[column]
        placements.append(
            {
                "x": x,
                "y": y,
                "card_height": int(card["card_height"]),
                "image": card["image"],
            }
        )
        column_heights[column] += int(card["card_height"]) + gap

    used_height = max(column_heights) - gap if placements else 0
    canvas_height = top_padding + used_height + bottom_padding
    canvas = _create_feast_background(canvas_width, canvas_height)
    card_palette = [
        (255, 255, 255, 236),
        (255, 250, 245, 236),
        (250, 245, 238, 236),
        (250, 248, 243, 236),
    ]

    for idx, placement in enumerate(placements):
        x = int(placement["x"])
        y = int(placement["y"])
        image = placement["image"]
        if not isinstance(image, Image.Image):
            continue
        card_height = int(placement["card_height"])
        shadow = Image.new("RGBA", (card_width + 60, card_height + 60), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (18, 18, 18 + card_width, 18 + card_height),
            radius=card_radius,
            fill=(83, 67, 47, 54),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        canvas.alpha_composite(shadow, dest=(x - 18, y - 10))

        card_layer = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card_layer)
        card_draw.rounded_rectangle(
            (0, 0, card_width - 1, card_height - 1),
            radius=card_radius,
            fill=card_palette[idx % len(card_palette)],
            outline=(228, 219, 206, 255),
            width=2,
        )
        chip_color = [
            (198, 128, 91, 255),
            (140, 164, 124, 255),
            (188, 150, 100, 255),
            (148, 133, 170, 255),
        ][idx % 4]
        card_draw.rounded_rectangle((16, 14, 78, 22), radius=4, fill=chip_color)

        image_x = (card_width - image.width) // 2
        image_y = card_padding
        image_mask = _create_rounded_mask((image.width, image.height), image_radius)
        image_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        image_layer.paste(image.convert("RGBA"), (0, 0))
        card_layer.paste(image_layer, (image_x, image_y), image_mask)
        canvas.alpha_composite(card_layer, dest=(x, y))

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    image_bytes, _, _ = maybe_compress_large_png(
        output.getvalue(),
        "image/png",
        log_prefix="吃大餐拼图",
    )
    return image_bytes


def _get_food_entry_context(
    group_id: str, food_id: str
) -> tuple[bool, dict[str, dict], dict] | None:
    normal_index = _load_index(group_id)
    normal_entry = normal_index.get(food_id)
    if isinstance(normal_entry, dict):
        return False, normal_index, normal_entry

    hidden_index = _load_index(group_id, hidden=True)
    hidden_entry = hidden_index.get(food_id)
    if isinstance(hidden_entry, dict):
        return True, hidden_index, hidden_entry
    return None


def _normalize_tag(tag: str | None) -> str | None:
    if not isinstance(tag, str):
        return None
    cleaned = tag.strip()
    return cleaned or None


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _get_tags(entry: dict) -> list[str]:
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return []
    result: list[str] = []
    for item in tags:
        normalized = _normalize_tag(item)
        if normalized:
            result.append(normalized)
    return _dedupe_keep_order(result)


def _get_hidden_owner(entry: dict) -> str:
    owner = entry.get("owner")
    return owner.strip() if isinstance(owner, str) else ""


def _normalize_rank(rank: str | None) -> str | None:
    if not isinstance(rank, str):
        return None
    cleaned = rank.strip()
    if not cleaned:
        return None
    return RANK_ALIASES.get(cleaned)


def _get_rank(entry: dict) -> str | None:
    rank = _normalize_rank(entry.get("rank"))
    return rank if rank in VALID_RANKS else None


def _ensure_labels_initialized(group_id: str) -> list[str]:
    labels_file = _get_labels_file(group_id)
    if labels_file.exists():
        return _load_labels(group_id)
    index = _load_all_indexes(group_id)
    labels: list[str] = []
    for entry in index.values():
        if isinstance(entry, dict):
            labels.extend(_get_tags(entry))
    deduped = _dedupe_keep_order(labels)
    _save_labels(group_id, deduped)
    return deduped


def _load_labels(group_id: str) -> list[str]:
    labels_file = _get_labels_file(group_id)
    if not labels_file.exists():
        return _ensure_labels_initialized(group_id)
    try:
        with open(labels_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        labels = raw.get("labels") if isinstance(raw, dict) else []
        if not isinstance(labels, list):
            return []
        result: list[str] = []
        for item in labels:
            normalized = _normalize_tag(item)
            if normalized:
                result.append(normalized)
        return _dedupe_keep_order(result)
    except Exception:
        logger.exception("读取标签索引失败")
        return []


def _save_labels(group_id: str, labels: list[str]) -> None:
    labels_file = _get_labels_file(group_id)
    labels_file.parent.mkdir(parents=True, exist_ok=True)
    with open(labels_file, "w", encoding="utf-8") as f:
        json.dump({"labels": _dedupe_keep_order(labels)}, f, ensure_ascii=False, indent=2)


def _record_labels(group_id: str, labels: list[str]) -> None:
    normalized = [tag for tag in (_normalize_tag(tag) for tag in labels) if tag]
    if not normalized:
        _ensure_labels_initialized(group_id)
        return
    existing = _ensure_labels_initialized(group_id)
    merged = _dedupe_keep_order(existing + normalized)
    if merged != existing:
        _save_labels(group_id, merged)


def _parse_tags_expr(expr: str | None) -> list[str]:
    if not isinstance(expr, str) or not expr.strip():
        return []
    tags = [_normalize_tag(match.group(1)) for match in _TAG_PARENS_RE.finditer(expr)]
    return _dedupe_keep_order([tag for tag in tags if tag])


def _parse_collect_text(text: str) -> tuple[str | None, str | None, list[str], str | None]:
    if not text:
        return None, None, [], None
    rest = text.strip()
    tags: list[str] = []
    rank: str | None = None

    tag_match = _COLLECT_TAGS_RE.search(rest)
    if tag_match:
        tags = _parse_tags_expr(tag_match.group(1))
        rest = (rest[: tag_match.start()] + " " + rest[tag_match.end() :]).strip()

    rank_match = _COLLECT_RANK_RE.search(rest)
    if rank_match:
        rank = _normalize_rank(rank_match.group(1))
        rest = (rest[: rank_match.start()] + " " + rest[rank_match.end() :]).strip()
    elif "#" in rest:
        hash_match = re.search(r"(?:^|\s)#(\S+)", rest)
        if hash_match:
            return None, None, [], "等级仅支持：夯 / 夯爆了 / 人上人 / NPC / 拉 / 拉完了"

    name = rest.strip() or None
    return name, rank, tags, None


def _parse_reply_edit_text(text: str) -> tuple[str | None, str | None, list[str] | None, str | None]:
    m = _REPLY_EDIT_RE.match(text)
    if not m:
        return None, None, None, "用法：引用食物消息后发送 % <名字> -r <等级> -t （标签1）（标签2）"

    body = (m.group("body") or "").strip()
    rank = _normalize_rank(m.group("rank"))
    if m.group("rank") and not rank:
        return None, None, None, "等级仅支持：夯 / 夯爆了 / 人上人 / NPC / 拉 / 拉完了"

    tags_expr = m.group("tags")
    tags = _parse_tags_expr(tags_expr) if tags_expr is not None else None
    name = body or None
    if name and name.startswith("-"):
        return None, None, None, "名字请写在 % 后面，选项使用 -r / -t"
    if not any([name, rank, tags is not None]):
        return None, None, None, "请至少提供名字、-r 或 -t 其中一项"
    return name, rank, tags, None


def _write_food_entry(
    group_id: str,
    index: dict[str, dict],
    food_id: str,
    *,
    name: str | None = None,
    rank: str | None = None,
    tags: list[str] | None = None,
    owner: str | None = None,
) -> None:
    entry = index.setdefault(food_id, {})
    if name is not None:
        entry["name"] = name
    if rank is not None:
        entry["rank"] = rank
    if tags is not None:
        entry["tags"] = _dedupe_keep_order(
            [tag for tag in (_normalize_tag(tag) for tag in tags) if tag]
        )
        _record_labels(group_id, entry["tags"])
    if owner is not None:
        entry["owner"] = owner


def _format_food_label(short_id: str, name: str | None) -> str:
    if name and name.strip():
        return f"『{name.strip()}』（{short_id}）"
    return f"『{short_id}』"


def _format_rank_suffix(rank: str | None) -> str:
    return f" #{rank}" if rank else ""


def _format_tags_suffix(tags: list[str]) -> str:
    if not tags:
        return ""
    return " %" + "".join(f"（{tag}）" for tag in tags)


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    images: list[MessageSegment] = []
    for seg in args:
        if seg.type == "image":
            images.append(seg)
    if event.reply:
        reply_images: list[MessageSegment] = []
        if event.reply.message:
            reply_images = [seg for seg in event.reply.message if seg.type == "image"]
        if not reply_images:
            try:
                msg_data = await bot.get_msg(message_id=event.reply.message_id)
                raw_msg = msg_data.get("message", [])
                if isinstance(raw_msg, Message):
                    reply_images = [seg for seg in raw_msg if seg.type == "image"]
                elif isinstance(raw_msg, str):
                    parsed = Message(raw_msg)
                    reply_images = [seg for seg in parsed if seg.type == "image"]
                elif isinstance(raw_msg, list):
                    for seg in raw_msg:
                        if isinstance(seg, MessageSegment) and seg.type == "image":
                            reply_images.append(seg)
                        elif isinstance(seg, dict) and seg.get("type") == "image":
                            reply_images.append(
                                MessageSegment("image", seg.get("data", {}))
                            )
            except Exception:
                pass
        images.extend(reply_images)
    return images


def _parse_food_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    m = _FOOD_ID_PREFIX_RE.search(text)
    if m:
        part = m.group(1)
        for sid in re.split(r"\s*[、]\s*", part):
            sid = sid.strip()
            if sid and sid not in ids:
                ids.append(sid)
    for m in _FOOD_LABEL_RE.finditer(text):
        sid = m.group(1).strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


async def _extract_food_ids_from_reply(bot: Bot, event: GroupMessageEvent) -> list[str]:
    if not event.reply:
        return []
    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw = msg_data.get("message", [])
        if isinstance(raw, str):
            text = Message(raw).extract_plain_text()
        elif isinstance(raw, list):
            text = "".join(
                s.get("data", {}).get("text", "")
                for s in raw
                if isinstance(s, dict) and s.get("type") == "text"
            )
        else:
            return []
        return _parse_food_ids_from_text(text.strip())
    except Exception:
        return []


def delete_food(group_id: str, food_id: str) -> bool:
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        return False
    hidden, index, entry = context
    filepath = _get_food_image_path(group_id, food_id, entry, hidden=hidden)
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index, hidden=hidden)
    return True


def _has_hidden_been_selected(entry: dict) -> bool:
    return bool(entry.get("hidden_selected"))


def _get_hidden_food_ids(index: dict[str, dict], *, only_unrandomed: bool = False) -> list[str]:
    result: list[str] = []
    for sid, entry in index.items():
        if only_unrandomed and _has_hidden_been_selected(entry):
            continue
        result.append(sid)
    return result


def _match_hidden_owner(group_id: str, food_name: str | None) -> str:
    if not isinstance(food_name, str) or not food_name.strip():
        return ""
    name = food_name.strip()
    matched: list[tuple[int, int, str]] = []
    for owner, aliases in _load_hidden_owners(group_id).items():
        tokens = [owner, *aliases]
        for token in tokens:
            if token and token in name:
                matched.append((len(token), 1 if token == owner else 0, owner))
    if not matched:
        return ""
    matched.sort(reverse=True)
    return matched[0][2]


def _hidden_owner_mode_enabled(group_id: str) -> bool:
    return _get_hidden_owners_file(group_id).exists()


def _clear_hidden_selected_marks_for_owner(index: dict[str, dict], owner: str) -> bool:
    changed = False
    for entry in index.values():
        if _get_hidden_owner(entry) != owner:
            continue
        if "hidden_selected" in entry:
            entry.pop("hidden_selected", None)
            changed = True
    return changed


def _pick_hidden_food_for_owner(
    index: dict[str, dict], owner: str
) -> tuple[str | None, bool]:
    owner_ids = [sid for sid, entry in index.items() if _get_hidden_owner(entry) == owner]
    if not owner_ids:
        return None, False

    if all(_has_hidden_been_selected(index[sid]) for sid in owner_ids):
        changed = _clear_hidden_selected_marks_for_owner(index, owner)
    else:
        changed = False

    weights = [
        0.5 if _has_hidden_been_selected(index[sid]) else 1.0
        for sid in owner_ids
    ]
    short_id = random.choices(owner_ids, weights=weights, k=1)[0]
    entry = index[short_id]
    if not _has_hidden_been_selected(entry):
        entry["hidden_selected"] = True
        changed = True
    return short_id, changed


def _pick_hidden_food_with_owner_mode(
    group_id: str, index: dict[str, dict]
) -> tuple[str | None, bool]:
    owners = _load_hidden_owners(group_id)
    up_state = _load_hidden_up_state(group_id)
    up_owner = str(up_state.get("up") or "").strip()
    guaranteed = bool(up_state.get("guaranteed"))

    owner_pool = {
        owner
        for owner in owners.keys()
        if any(_get_hidden_owner(entry) == owner for entry in index.values())
    }
    if up_owner and up_owner not in owner_pool:
        up_owner = ""
        guaranteed = False

    chosen_owner = ""
    if up_owner:
        if guaranteed:
            chosen_owner = up_owner
            guaranteed = False
        elif random.random() < 0.5:
            chosen_owner = up_owner
        else:
            guaranteed = True

    if chosen_owner:
        short_id, index_changed = _pick_hidden_food_for_owner(index, chosen_owner)
        _save_hidden_up_state(group_id, {"up": up_owner, "guaranteed": guaranteed})
        return short_id, index_changed

    if not owner_pool:
        _save_hidden_up_state(group_id, {"up": up_owner, "guaranteed": guaranteed})
        return None, False
    random_owner = random.choice(sorted(owner_pool))
    short_id, changed = _pick_hidden_food_for_owner(index, random_owner)
    _save_hidden_up_state(group_id, {"up": up_owner, "guaranteed": guaranteed})
    return short_id, changed


def _clear_hidden_selected_marks(index: dict[str, dict]) -> bool:
    changed = False
    for entry in index.values():
        if "hidden_selected" in entry:
            entry.pop("hidden_selected", None)
            changed = True
    return changed


def _pick_hidden_food_for_random(
    group_id: str, index: dict[str, dict]
) -> tuple[str | None, bool]:
    if _hidden_owner_mode_enabled(group_id):
        return _pick_hidden_food_with_owner_mode(group_id, index)

    hidden_ids = _get_hidden_food_ids(index)
    if not hidden_ids:
        return None, False

    available_ids = _get_hidden_food_ids(index, only_unrandomed=True)
    changed = False
    if not available_ids:
        changed = _clear_hidden_selected_marks(index)
        available_ids = hidden_ids

    short_id = random.choice(available_ids)
    entry = index[short_id]
    if not _has_hidden_been_selected(entry):
        entry["hidden_selected"] = True
        changed = True
    return short_id, changed


def _roll_hidden_food_once(
    group_id: str,
    hidden_index: dict[str, dict],
    current_prob: int,
) -> tuple[str | None, int, bool]:
    """执行一次隐藏食物判定，返回 (抽中的隐藏食物ID, 新概率, 隐藏索引是否被修改)。"""
    if not hidden_index:
        return None, current_prob, False
    if random.randint(1, 100) <= current_prob:
        short_id, index_changed = _pick_hidden_food_for_random(group_id, hidden_index)
        return short_id, 3, index_changed
    return None, min(100, current_prob + 1), False


def _resolve_id_or_name(
    group_id: str, id_or_name: str, *, allow_dup: bool = False
) -> tuple[list[str] | None, str | None]:
    index = _load_all_indexes(group_id)
    if not id_or_name or not id_or_name.strip():
        return None, "请输入食物ID或名字"

    key = id_or_name.strip()
    if key in index:
        return [key], None

    matched: list[str] = []
    for sid, entry in index.items():
        name = entry.get("name")
        if name and str(name).strip() == key:
            matched.append(sid)

    if not matched:
        return None, f"未找到名为『{key}』或ID为『{key}』的食物"
    if len(matched) == 1:
        return matched, None
    if allow_dup:
        return matched, None
    ids_text = "\n".join(matched)
    return None, f"『{key}』对应的记录有：\n{ids_text}\n请使用id操作。"


def _extract_image_urls_from_segments(images: list[MessageSegment]) -> list[str]:
    urls: list[str] = []
    for seg in images:
        if seg.type != "image":
            continue
        url = seg.data.get("url")
        if url:
            urls.append(url)
    return urls


def _extract_collect_command(text: str) -> tuple[str, str] | None:
    m = _COLLECT_COMMAND_RE.search(text)
    if not m:
        return None
    return m.group("command"), text[m.end() :].strip()


async def _download_food_images(
    group_id: str, image_urls: list[str]
) -> list[tuple[str, bytes, str | None]] | None:
    index = _load_all_indexes(group_id)
    existing_ids = set(index.keys())
    downloaded: list[tuple[str, bytes, str | None]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for url in image_urls:
            try:
                resp = await client.get(url, timeout=30)
                resp.raise_for_status()
                short_id = _generate_short_id(existing_ids)
                existing_ids.add(short_id)
                image_bytes, content_type, _ = maybe_compress_large_png(
                    resp.content,
                    resp.headers.get("content-type"),
                    log_prefix=f"食物图片压缩[{short_id}]",
                )
                downloaded.append(
                    (short_id, image_bytes, content_type)
                )
            except Exception:
                logger.exception(f"下载食物图片失败: {url}")
                return None
    return downloaded


async def _resolve_food_metadata(
    downloaded: list[tuple[str, bytes, str | None]],
    name: str | None,
    *,
    name_only_with_llm: bool,
    log_prefix: str,
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    if name:
        for short_id, _, _ in downloaded:
            resolved[short_id] = {"name": name, "tags": []}
        return resolved

    if name_only_with_llm:
        name_results = await asyncio.gather(
            *[
                suggest_food_name_from_image_bytes(
                    content,
                    content_type,
                    log_prefix=log_prefix,
                )
                for _, content, content_type in downloaded
            ]
        )
        for (short_id, _, _), auto_name in zip(downloaded, name_results):
            resolved[short_id] = {"name": auto_name, "tags": []}
        return resolved

    recog_results = await asyncio.gather(
        *[
            recognize_food_from_image_bytes(
                content,
                content_type,
                log_prefix=log_prefix,
            )
            for _, content, content_type in downloaded
        ]
    )
    for (short_id, _, _), (rec_type, auto_name) in zip(downloaded, recog_results):
        resolved[short_id] = {
            "name": auto_name if rec_type == "FOOD" else None,
            "tags": [],
        }
    return resolved


def _build_collect_food_success_message(
    downloaded: list[tuple[str, bytes, str | None]],
    resolved: dict[str, dict[str, Any]],
    *,
    manual_name: str | None,
    manual_rank: str | None,
    manual_tags: list[str],
    hidden: bool,
) -> str:
    id_str = "、".join(sid for sid, _, _ in downloaded)
    lines: list[str] = []
    food_type = "隐藏食物图" if hidden else "食物图"
    if manual_name:
        header = f"已保存 {len(downloaded)} 张{food_type}✓ 『{manual_name}』"
        header += _format_rank_suffix(manual_rank)
        header += _format_tags_suffix(manual_tags)
        lines.append(header)
    else:
        auto_named_count = sum(1 for item in resolved.values() if item.get("name"))
        if auto_named_count:
            lines.append(
                f"已保存 {len(downloaded)} 张{food_type}✓ （已自动命名 {auto_named_count} 张，名称仅供参考，可用 /补充名字 <id> <名字> 调整）"
            )
        else:
            lines.append(
                f"已保存 {len(downloaded)} 张{food_type}✓ （未指定名字，且自动命名失败，可用 /补充名字 <id> <名字> 补充）"
            )
        for short_id, _, _ in downloaded:
            item = resolved.get(short_id, {})
            item_name = item.get("name")
            item_tags = item.get("tags") or []
            label_line = _format_food_label(short_id, item_name)
            if item_tags:
                label_line += _format_tags_suffix(item_tags)
            lines.append(label_line)
    lines.append(f"食物ID：{id_str}")
    return "\n".join(lines)


async def save_foods_from_image_urls(
    group_id: str,
    image_urls: list[str],
    name: str | None = None,
    *,
    rank: str | None = None,
    tags: list[str] | None = None,
    hidden: bool = False,
    name_only_with_llm: bool = False,
    log_prefix: str = "收集食物自动命名",
) -> str | None:
    if not image_urls:
        return None

    normalized_rank = _normalize_rank(rank)
    normalized_tags = _dedupe_keep_order(
        [tag for tag in (_normalize_tag(tag) for tag in (tags or [])) if tag]
    )

    downloaded = await _download_food_images(group_id, image_urls)
    if not downloaded:
        return None

    resolved = await _resolve_food_metadata(
        downloaded,
        name,
        name_only_with_llm=name_only_with_llm,
        log_prefix=log_prefix,
    )

    index = _load_index(group_id, hidden=hidden)
    images_dir = _get_hidden_images_dir(group_id) if hidden else _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        for short_id, content, _ in downloaded:
            item = resolved.get(short_id, {})
            entry_name = name if name is not None else item.get("name")
            entry_tags = normalized_tags if tags is not None else item.get("tags", [])
            owner = _match_hidden_owner(group_id, entry_name) if hidden else ""
            index[short_id] = {"filename": f"{short_id}.png"}
            _write_food_entry(
                group_id,
                index,
                short_id,
                name=entry_name,
                rank=normalized_rank,
                tags=entry_tags,
                owner=owner if hidden else None,
            )
            filepath = images_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(group_id, index, hidden=hidden)
    except Exception:
        logger.exception("保存食物文件失败，回滚")
        for short_id, _, _ in downloaded:
            (images_dir / f"{short_id}.png").unlink(missing_ok=True)
            index.pop(short_id, None)
        return None

    return _build_collect_food_success_message(
        downloaded,
        resolved,
        manual_name=name,
        manual_rank=normalized_rank,
        manual_tags=normalized_tags,
        hidden=hidden,
    )


async def add_food_from_image_url(
    group_id: str,
    image_url: str,
    name: str | None,
    *,
    rank: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    result = await save_foods_from_image_urls(
        group_id,
        [image_url],
        name=name,
        rank=rank,
        tags=tags,
        log_prefix="自动食物收集",
    )
    return result


def _collect_food_image_first_rule(event: GroupMessageEvent) -> bool:
    msg = event.get_message()
    if not msg or msg[0].is_text():
        return False
    text = msg.extract_plain_text().strip()
    if not text:
        return False
    command = _extract_collect_command(text)
    if not command or command[0] != "收集食物":
        return False
    has_image = any(seg.type == "image" for seg in msg)
    if not has_image and not (
        event.reply
        and event.reply.message
        and any(seg.type == "image" for seg in event.reply.message)
    ):
        return False
    return True


def _collect_hidden_food_image_first_rule(event: GroupMessageEvent) -> bool:
    msg = event.get_message()
    if not msg or msg[0].is_text():
        return False
    text = msg.extract_plain_text().strip()
    if not text:
        return False
    command = _extract_collect_command(text)
    if not command or command[0] != "收集隐藏食物":
        return False
    has_image = any(seg.type == "image" for seg in msg)
    if not has_image and not (
        event.reply
        and event.reply.message
        and any(seg.type == "image" for seg in event.reply.message)
    ):
        return False
    return True


def _reply_percent_rule(event: GroupMessageEvent) -> bool:
    text = event.get_message().extract_plain_text().strip()
    return bool(event.reply and text.startswith("%"))


collect_food_cmd = on_command("收集食物", priority=10, block=True)
collect_hidden_food_cmd = on_command(
    "收集隐藏食物",
    priority=10,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)
collect_food_image_first_cmd = on_message(
    _collect_food_image_first_rule, priority=9, block=True
)
collect_hidden_food_image_first_cmd = on_message(
    _collect_hidden_food_image_first_rule,
    priority=9,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)
reply_percent_cmd = on_message(_reply_percent_rule, priority=10, block=True)
delete_food_cmd = on_command("删除食物", priority=10, block=True, permission=SUPERUSER)
supplement_name_cmd = on_command("补充名字", priority=10, block=True)
hidden_food_cmd = on_command(
    "隐藏", priority=10, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER
)
up_cmd = on_command("up", priority=10, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER)
feast_cmd = on_command("吃大餐", priority=10, block=True)
eat_cmd = on_command("吃", priority=10, block=True)
tag_food_cmd = on_command("标记", priority=10, block=True)
what_to_eat_matcher = on_keyword({"吃什么"}, priority=50, block=False)


async def _do_collect_food(
    bot: Bot,
    event: GroupMessageEvent,
    *,
    name: str | None,
    rank: str | None,
    tags: list[str],
    args: Message,
    matcher: Matcher,
    hidden: bool = False,
) -> None:
    images = await _extract_images(bot, event, args)
    if not images:
        command_name = "/收集隐藏食物" if hidden else "/收集食物"
        await matcher.finish(
            f"请在命令中附带图片或引用含图片的消息，例如：{command_name} (名字) [图片]"
        )

    group_id = str(event.group_id)
    image_urls = _extract_image_urls_from_segments(images)
    if not image_urls:
        await matcher.finish("未获取到有效图片，请检查后重试")

    result = await save_foods_from_image_urls(
        group_id,
        image_urls,
        name,
        rank=rank,
        tags=tags,
        hidden=hidden,
        # 用户主动收集时，默认图片就是待收录食物，直接要求模型命名。
        name_only_with_llm=True,
        log_prefix="手动收集食物自动命名",
    )
    if not result:
        await matcher.finish("保存失败，已回滚（未修改数据），请稍后重试")
    await matcher.finish(result)


@collect_food_cmd.handle()
async def handle_collect_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    name, rank, tags, err = _parse_collect_text(text)
    if err:
        await collect_food_cmd.finish(err)
    await _do_collect_food(
        bot, event, name=name, rank=rank, tags=tags, args=args, matcher=collect_food_cmd
    )


@collect_hidden_food_cmd.handle()
async def handle_collect_hidden_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    name, rank, tags, err = _parse_collect_text(text)
    if err:
        await collect_hidden_food_cmd.finish(err)
    await _do_collect_food(
        bot,
        event,
        name=name,
        rank=rank,
        tags=tags,
        args=args,
        matcher=collect_hidden_food_cmd,
        hidden=True,
    )


@collect_food_image_first_cmd.handle()
async def handle_collect_food_image_first(bot: Bot, event: GroupMessageEvent):
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    command = _extract_collect_command(text)
    collect_text = command[1] if command else ""
    name, rank, tags, err = _parse_collect_text(collect_text)
    if err:
        await collect_food_image_first_cmd.finish(err)
    await _do_collect_food(
        bot,
        event,
        name=name,
        rank=rank,
        tags=tags,
        args=msg,
        matcher=collect_food_image_first_cmd,
    )


@collect_hidden_food_image_first_cmd.handle()
async def handle_collect_hidden_food_image_first(
    bot: Bot, event: GroupMessageEvent
):
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    command = _extract_collect_command(text)
    collect_text = command[1] if command else ""
    name, rank, tags, err = _parse_collect_text(collect_text)
    if err:
        await collect_hidden_food_image_first_cmd.finish(err)
    await _do_collect_food(
        bot,
        event,
        name=name,
        rank=rank,
        tags=tags,
        args=msg,
        matcher=collect_hidden_food_image_first_cmd,
        hidden=True,
    )


@reply_percent_cmd.handle()
async def handle_reply_percent(
    bot: Bot, event: GroupMessageEvent
):
    reply_ids = await _extract_food_ids_from_reply(bot, event)
    if len(reply_ids) != 1:
        await reply_percent_cmd.finish("请引用一条只包含一个食物 ID 的食物消息后再使用 %")

    text = event.get_message().extract_plain_text().strip()
    name, rank, tags, err = _parse_reply_edit_text(text)
    if err:
        await reply_percent_cmd.finish(err)

    group_id = str(event.group_id)
    food_id = reply_ids[0]
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        await reply_percent_cmd.finish(f"食物ID『{food_id}』不存在")
    hidden, index, _ = context

    _write_food_entry(
        group_id,
        index,
        food_id,
        name=name if name is not None else None,
        rank=rank if rank is not None else None,
        tags=tags if tags is not None else None,
    )
    _save_index(group_id, index, hidden=hidden)

    entry = index[food_id]
    parts: list[str] = [f"已更新食物（ID：{food_id}）"]
    if name is not None:
        parts.append(f"名字：『{entry.get('name') or food_id}』")
    if rank is not None:
        parts.append(f"等级：{_get_rank(entry)}")
    if tags is not None:
        parts.append(
            "标签：" + ("、".join(_get_tags(entry)) if _get_tags(entry) else "（空）")
        )
    await reply_percent_cmd.finish("，".join(parts) + "✓")


@delete_food_cmd.handle()
async def handle_delete_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    if not text and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            text = reply_ids[0]
    if not text:
        await delete_food_cmd.finish(
            "请输入要删除的食物ID或名字，或引用食物消息后直接发送 /删除食物"
        )

    group_id = str(event.group_id)
    ids, err = _resolve_id_or_name(group_id, text, allow_dup=False)
    if err:
        await delete_food_cmd.finish(err)

    food_id = ids[0]
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        await delete_food_cmd.finish(f"食物ID『{food_id}』不存在")
    hidden, index, entry = context
    filepath = _get_food_image_path(group_id, food_id, entry, hidden=hidden)
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index, hidden=hidden)
    await delete_food_cmd.finish(f"已删除食物（ID：{food_id}）✓")


@supplement_name_cmd.handle()
async def handle_supplement_name(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    id_or_name: str | None = None
    new_name: str | None = None
    if len(parts) >= 2:
        id_or_name, new_name = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1 and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            id_or_name, new_name = reply_ids[0], parts[0].strip()
    if not id_or_name or not new_name:
        await supplement_name_cmd.finish(
            "用法：/补充名字 <食物ID或名字> <新名字>，或引用食物消息后发送 /补充名字 新名字"
        )

    group_id = str(event.group_id)
    ids, err = _resolve_id_or_name(group_id, id_or_name, allow_dup=False)
    if err:
        await supplement_name_cmd.finish(err)

    food_id = ids[0]
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        await supplement_name_cmd.finish(f"食物ID『{food_id}』不存在")
    hidden, index, _ = context
    _write_food_entry(group_id, index, food_id, name=new_name)
    _save_index(group_id, index, hidden=hidden)
    await supplement_name_cmd.finish(
        f"已为食物（ID：{food_id}）补充名字『{new_name}』✓"
    )


@hidden_food_cmd.handle()
async def handle_hidden_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    food_id = args.extract_plain_text().strip()
    if not food_id and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            food_id = reply_ids[0]
    if not food_id:
        await hidden_food_cmd.finish("用法：/隐藏 <食物ID>，或引用食物消息后直接发送 /隐藏")

    group_id = str(event.group_id)
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        await hidden_food_cmd.finish(f"食物ID『{food_id}』不存在")
    hidden, index, entry = context
    if hidden:
        await hidden_food_cmd.finish(f"食物（ID：{food_id}）已经是隐藏食物")

    hidden_index = _load_index(group_id, hidden=True)
    hidden_images_dir = _get_hidden_images_dir(group_id)
    hidden_images_dir.mkdir(parents=True, exist_ok=True)
    src_path = _get_food_image_path(group_id, food_id, entry, hidden=False)
    dest_path = _get_food_image_path(group_id, food_id, entry, hidden=True)
    original_entry = dict(entry)
    try:
        if src_path.exists():
            src_path.replace(dest_path)
        entry["owner"] = _match_hidden_owner(group_id, entry.get("name"))
        hidden_index[food_id] = entry
        del index[food_id]
        _save_index(group_id, index, hidden=False)
        _save_index(group_id, hidden_index, hidden=True)
    except Exception:
        logger.exception("移动隐藏食物失败")
        index[food_id] = original_entry
        hidden_index.pop(food_id, None)
        try:
            _save_index(group_id, index, hidden=False)
            _save_index(group_id, hidden_index, hidden=True)
        except Exception:
            logger.exception("回滚隐藏食物索引失败")
        if dest_path.exists() and not src_path.exists():
            try:
                dest_path.replace(src_path)
            except Exception:
                logger.exception("回滚隐藏食物图片失败")
        await hidden_food_cmd.finish("移动为隐藏食物失败，请稍后重试")
    await hidden_food_cmd.finish(f"已将食物（ID：{food_id}）设为隐藏食物✓")


@up_cmd.handle()
async def handle_up(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = str(event.group_id)
    if not _hidden_owner_mode_enabled(group_id):
        await up_cmd.finish("本群未启用 hidden owner / up 机制")

    raw = args.extract_plain_text().strip()
    if not raw:
        state = _load_hidden_up_state(group_id)
        current_up = str(state.get("up") or "").strip() or "（空）"
        guaranteed = "是" if bool(state.get("guaranteed")) else "否"
        await up_cmd.finish(f"当前 up：{current_up}\n保底：{guaranteed}")

    if raw == "clear":
        _save_hidden_up_state(group_id, {"up": "", "guaranteed": False})
        await up_cmd.finish("已清空当前 up✓")

    owners = _load_hidden_owners(group_id)
    if raw not in owners:
        choices = "、".join(owners.keys()) if owners else "（无可用 owner）"
        await up_cmd.finish(f"未知 owner：{raw}\n可用 owner：{choices}")

    _save_hidden_up_state(group_id, {"up": raw, "guaranteed": False})
    await up_cmd.finish(f"已将当前 up 设置为『{raw}』✓")


@tag_food_cmd.handle()
async def handle_tag_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    id_or_name: str | None = None
    tag: str | None = None
    if len(parts) >= 2:
        id_or_name, tag = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1 and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            id_or_name, tag = reply_ids[0], parts[0].strip()
    tag = _normalize_tag(tag)
    if not id_or_name or not tag:
        await tag_food_cmd.finish(
            "用法：/标记 <食物ID或名字> <标签>，或引用食物消息后发送 /标记 标签"
        )

    group_id = str(event.group_id)
    ids, err = _resolve_id_or_name(group_id, id_or_name, allow_dup=False)
    if err:
        await tag_food_cmd.finish(err)

    food_id = ids[0]
    context = _get_food_entry_context(group_id, food_id)
    if context is None:
        await tag_food_cmd.finish(f"食物ID『{food_id}』不存在")
    hidden, index, entry = context
    entry = index[food_id]
    tags = _get_tags(entry)
    if tag not in tags:
        tags.append(tag)
        _write_food_entry(group_id, index, food_id, tags=tags)
        _save_index(group_id, index, hidden=hidden)
    await tag_food_cmd.finish(f"已为食物（ID：{food_id}）添加标签『{tag}』✓")


def _normalize_search_text(text: str) -> str:
    cleaned = text.strip().casefold()
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    cleaned = re.sub(r"\s+", "", cleaned)
    mapped_rank = _normalize_rank(text.strip())
    if mapped_rank:
        return mapped_rank.casefold()
    return cleaned


def _score_field(query: str, field: str) -> float:
    if not query or not field:
        return 0.0
    if query == field:
        return 1.0
    if field.startswith(query):
        return 0.88
    if query in field:
        return 0.78
    return SequenceMatcher(None, query, field).ratio()


def _search_food_candidates(
    index: dict[str, dict], query: str
) -> list[str]:
    normalized_query = _normalize_search_text(query)
    scored: list[tuple[float, str]] = []
    for sid, entry in index.items():
        best = 0.0
        id_score = _score_field(normalized_query, _normalize_search_text(sid)) * 0.7
        best = max(best, id_score)
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            best = max(best, _score_field(normalized_query, _normalize_search_text(name)) * 1.0)
        rank = _get_rank(entry)
        if rank:
            best = max(best, _score_field(normalized_query, _normalize_search_text(rank)) * 0.82)
        for tag in _get_tags(entry):
            best = max(best, _score_field(normalized_query, _normalize_search_text(tag)) * 0.9)
        if best >= EAT_SCORE_THRESHOLD:
            scored.append((best, sid))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [sid for _, sid in scored[:MAX_EAT_CANDIDATES]]


def _build_food_message(
    group_id: str,
    food_id: str,
    index: dict[str, dict],
    *,
    hidden: bool = False,
) -> Message | str:
    entry = index[food_id]
    name = entry.get("name") or None
    label = _format_food_label(food_id, name)
    rank = _get_rank(entry)
    tags = _get_tags(entry)
    caption = f"请你吃{label}"
    if rank:
        caption += f" #{rank}"
    if tags:
        caption += "\n标签：" + "、".join(tags)
    filepath = _get_food_image_path(group_id, food_id, entry, hidden=hidden)
    if not filepath.exists():
        return f"食物图片不存在（ID：{food_id}）"
    return MessageSegment.text(caption + "\n") + MessageSegment.image(filepath.read_bytes())


async def _send_hidden_food_reveal(
    bot: Bot,
    event: GroupMessageEvent,
    group_id: str,
    food_id: str,
    entry: dict,
) -> None:
    """发送隐藏食物提示，并在短时间后撤回图片。"""
    name = entry.get("name") or food_id
    food_name = name.strip() if isinstance(name, str) and name.strip() else food_id
    filepath = _get_food_image_path(group_id, food_id, entry, hidden=True)
    if not filepath.exists():
        return

    user_id = event.get_user_id()
    text_msg = (
        MessageSegment.text("恭喜")
        + MessageSegment.at(user_id)
        + MessageSegment.text(f"，请您享用{food_name}：")
    )
    await bot.send(event, text_msg)
    img_resp = await bot.send(event, MessageSegment.image(filepath.read_bytes()))

    async def _recall_image():
        await asyncio.sleep(5)
        try:
            msg_id = None
            if isinstance(img_resp, dict):
                msg_id = img_resp.get("message_id") or (img_resp.get("data") or {}).get(
                    "message_id"
                )
            if msg_id is not None:
                await bot.call_api("delete_msg", message_id=msg_id)
        except Exception as e:
            logger.warning(f"撤回隐藏食物图片失败: {e}")

    asyncio.create_task(_recall_image())


@eat_cmd.handle()
async def handle_eat(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    if not text and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if reply_ids:
            text = reply_ids[0]
    if text == "什么":
        await _handle_what_to_eat(bot, event)
        await eat_cmd.finish()
    if not text:
        await eat_cmd.finish("用法：/吃 <食物ID/名字/标签/等级>，或引用食物消息后直接发送 /吃")

    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        await eat_cmd.finish("本群还没有收集任何食物，使用 /收集食物 [名字] [图片] 来添加吧")

    candidates = _search_food_candidates(index, text)
    if not candidates:
        await eat_cmd.finish(f"未找到和『{text}』相近的食物")

    chosen_id = random.choice(candidates)
    msg = _build_food_message(group_id, chosen_id, index, hidden=False)
    await eat_cmd.finish(msg)


@feast_cmd.handle()
async def handle_feast(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    text = args.extract_plain_text().strip()
    try:
        count = int(text) if text else 3
    except ValueError:
        count = 3
    count = max(1, min(MAX_FEAST_COUNT, count))

    index = _load_index(group_id)
    if not index:
        await feast_cmd.finish(
            "本群还没有收集任何食物，使用 /收集食物 [名字] [图片] 来添加吧"
        )

    ids = list(index.keys())
    if not ids:
        await feast_cmd.finish(
            "本群没有普通食物，吃大餐仅从普通食物中抽取（隐藏食物仅能从『吃什么』单抽获得）"
        )
    if len(ids) < count:
        await feast_cmd.finish(
            f"本群目前只有 {len(ids)} 道菜，无法凑齐 {count} 道，试试 /吃大餐 {len(ids)}"
        )

    hidden_index = _load_index(group_id, hidden=True)
    hidden_pick_id: str | None = None
    if _get_hidden_food_ids(hidden_index):
        prob = _load_hidden_prob(group_id)
        hidden_pick_id, prob, index_changed = _roll_hidden_food_once(group_id, hidden_index, prob)
        _save_hidden_prob(group_id, prob)
        if index_changed:
            _save_index(group_id, hidden_index, hidden=True)

    chosen_ids = random.sample(ids, count)
    collage_bytes = _render_feast_collage(group_id, chosen_ids, index)
    if not collage_bytes:
        await feast_cmd.finish("生成大餐拼图失败，请稍后重试")

    feast_message = (
        MessageSegment.at(event.get_user_id())
        + MessageSegment.text(" 请你吃大餐：\n")
        + MessageSegment.image(collage_bytes)
    )
    await bot.send(event, feast_message)

    if hidden_pick_id:
        entry = hidden_index.get(hidden_pick_id)
        if isinstance(entry, dict):
            await _send_hidden_food_reveal(bot, event, group_id, hidden_pick_id, entry)

    await feast_cmd.finish()


async def _handle_what_to_eat(bot: Bot, event: GroupMessageEvent) -> None:
    setattr(event, "_yiyin_skip_repetition", True)
    group_id = str(event.group_id)
    normal_index = _load_index(group_id)
    hidden_index = _load_index(group_id, hidden=True)
    if not normal_index and not hidden_index:
        return

    normal_ids = list(normal_index.keys())
    hidden_ids = _get_hidden_food_ids(hidden_index)

    hidden_pick_id: str | None = None
    if hidden_ids:
        prob = _load_hidden_prob(group_id)
        hidden_pick_id, prob, index_changed = _roll_hidden_food_once(group_id, hidden_index, prob)
        _save_hidden_prob(group_id, prob)
        if index_changed:
            _save_index(group_id, hidden_index, hidden=True)
    else:
        index_changed = False

    if hidden_pick_id:
        short_id = hidden_pick_id
        if not short_id:
            return
        entry = hidden_index[short_id]

        await bot.send(event, "是啊，吃什么")
        await _send_hidden_food_reveal(bot, event, group_id, short_id, entry)
        return

    if normal_ids:
        short_id = random.choice(normal_ids)
        entry = normal_index[short_id]
        hidden = False
    elif hidden_ids:
        short_id = random.choice(hidden_ids)
        entry = hidden_index[short_id]
        hidden = True
    else:
        return
    name = entry.get("name") or None
    label = _format_food_label(short_id, name)
    filepath = _get_food_image_path(group_id, short_id, entry, hidden=hidden)
    if not filepath.exists():
        return

    await bot.send(event, "是啊，吃什么")
    msg = (
        MessageSegment.text("请你吃")
        + MessageSegment.text(label)
        + MessageSegment.text("怎么样？\n")
        + MessageSegment.image(filepath.read_bytes())
    )
    await bot.send(event, msg)


@what_to_eat_matcher.handle()
async def handle_what_to_eat(bot: Bot, event: GroupMessageEvent):
    await _handle_what_to_eat(bot, event)
    await what_to_eat_matcher.finish()


from yiyin.food import auto_collect  # noqa: F401
