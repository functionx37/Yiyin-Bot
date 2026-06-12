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

from yiyin.food.llm_recognition import (
    recognize_food_with_labels_from_image_bytes,
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


def _get_images_dir(group_id: str) -> Path:
    return _get_group_dir(group_id) / "images"


def _get_hidden_prob_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_prob.json"


def _get_labels_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "label.json"


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


def _generate_short_id(existing_ids: set[str]) -> str:
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str) -> dict[str, dict]:
    index_file = _get_index_file(group_id)
    if not index_file.exists():
        return {}
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("读取食物索引失败")
        return {}


def _save_index(group_id: str, index: dict[str, dict]) -> None:
    index_file = _get_index_file(group_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


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
    index = _load_index(group_id)
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
    hidden: bool | None = None,
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
    if hidden is not None:
        entry["hidden"] = hidden


def get_group_labels(group_id: str) -> list[str]:
    return _load_labels(group_id)


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
    index = _load_index(group_id)
    if food_id not in index:
        return False
    entry = index[food_id]
    fn = entry.get("filename") or f"{food_id}.png"
    filepath = _get_images_dir(group_id) / fn
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index)
    return True


def _has_hidden_been_selected(entry: dict) -> bool:
    return bool(entry.get("hidden_selected"))


def _get_hidden_food_ids(
    index: dict[str, dict], *, only_unrandomed: bool = False
) -> list[str]:
    result: list[str] = []
    for sid, entry in index.items():
        if not entry.get("hidden"):
            continue
        if only_unrandomed and _has_hidden_been_selected(entry):
            continue
        result.append(sid)
    return result


def _clear_hidden_selected_marks(index: dict[str, dict]) -> bool:
    changed = False
    for entry in index.values():
        if not entry.get("hidden"):
            continue
        if "hidden_selected" in entry:
            entry.pop("hidden_selected", None)
            changed = True
    return changed


def _pick_hidden_food_for_random(index: dict[str, dict]) -> tuple[str | None, bool]:
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


def _resolve_id_or_name(
    group_id: str, id_or_name: str, *, allow_dup: bool = False
) -> tuple[list[str] | None, str | None]:
    index = _load_index(group_id)
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
    index = _load_index(group_id)
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
    label_pool: list[str] | None = None,
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
        label_results: list[dict[str, object]]
        if label_pool:
            label_results = await asyncio.gather(
                *[
                    recognize_food_with_labels_from_image_bytes(
                        content,
                        content_type,
                        label_pool=label_pool,
                        log_prefix=log_prefix,
                    )
                    for _, content, content_type in downloaded
                ]
            )
        else:
            label_results = [{"type": "OTHER", "tags": []} for _ in downloaded]
        for (short_id, _, _), auto_name, label_result in zip(
            downloaded, name_results, label_results
        ):
            resolved[short_id] = {
                "name": auto_name,
                "tags": label_result.get("tags", []) if isinstance(label_result, dict) else [],
            }
        return resolved

    recog_results = await asyncio.gather(
        *[
            recognize_food_with_labels_from_image_bytes(
                content,
                content_type,
                label_pool=label_pool or [],
                log_prefix=log_prefix,
            )
            for _, content, content_type in downloaded
        ]
    )
    for (short_id, _, _), result in zip(downloaded, recog_results):
        resolved[short_id] = {
            "name": result.get("name") if result.get("type") == "FOOD" else None,
            "tags": _dedupe_keep_order(
                [
                    tag
                    for tag in (
                        _normalize_tag(tag) for tag in result.get("tags", []) or []
                    )
                    if tag
                ]
            ),
        }
    return resolved


def _build_collect_food_success_message(
    downloaded: list[tuple[str, bytes, str | None]],
    resolved: dict[str, dict[str, Any]],
    *,
    manual_name: str | None,
    manual_rank: str | None,
    manual_tags: list[str],
    auto_tag_hint: bool,
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
        if auto_tag_hint and manual_tags:
            lines.append(f"已自动标记标签：{'、'.join(manual_tags)}")
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
        if auto_tag_hint:
            auto_tags = _dedupe_keep_order(
                [
                    tag
                    for short_id, _, _ in downloaded
                    for tag in resolved.get(short_id, {}).get("tags", [])
                ]
            )
            if auto_tags:
                lines.append(f"已自动标记标签：{'、'.join(auto_tags)}")
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
    auto_tag_with_llm: bool = False,
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

    label_pool = _load_labels(group_id) if auto_tag_with_llm else []
    resolved = await _resolve_food_metadata(
        downloaded,
        name,
        name_only_with_llm=name_only_with_llm,
        log_prefix=log_prefix,
        label_pool=label_pool,
    )

    index = _load_index(group_id)
    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        for short_id, content, _ in downloaded:
            item = resolved.get(short_id, {})
            entry_name = name if name is not None else item.get("name")
            entry_tags = normalized_tags if tags is not None else item.get("tags", [])
            index[short_id] = {"filename": f"{short_id}.png"}
            _write_food_entry(
                group_id,
                index,
                short_id,
                name=entry_name,
                rank=normalized_rank,
                tags=entry_tags,
                hidden=hidden,
            )
            filepath = images_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(group_id, index)
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
        auto_tag_hint=auto_tag_with_llm,
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
        auto_tag_with_llm=tags is not None,
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
    index = _load_index(group_id)
    if food_id not in index:
        await reply_percent_cmd.finish(f"食物ID『{food_id}』不存在")

    _write_food_entry(
        group_id,
        index,
        food_id,
        name=name if name is not None else None,
        rank=rank if rank is not None else None,
        tags=tags if tags is not None else None,
    )
    _save_index(group_id, index)

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
    index = _load_index(group_id)
    entry = index[food_id]
    fn = entry.get("filename") or f"{food_id}.png"
    filepath = _get_images_dir(group_id) / fn
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index)
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
    index = _load_index(group_id)
    _write_food_entry(group_id, index, food_id, name=new_name)
    _save_index(group_id, index)
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
    index = _load_index(group_id)
    if food_id not in index:
        await hidden_food_cmd.finish(f"食物ID『{food_id}』不存在")

    index[food_id]["hidden"] = True
    _save_index(group_id, index)
    await hidden_food_cmd.finish(f"已将食物（ID：{food_id}）设为隐藏食物✓")


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
    index = _load_index(group_id)
    entry = index[food_id]
    tags = _get_tags(entry)
    if tag not in tags:
        tags.append(tag)
        _write_food_entry(group_id, index, food_id, tags=tags)
        _save_index(group_id, index)
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
        if entry.get("hidden"):
            continue
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


def _build_food_message(group_id: str, food_id: str, index: dict[str, dict]) -> Message | str:
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
    fn = entry.get("filename") or f"{food_id}.png"
    filepath = _get_images_dir(group_id) / fn
    if not filepath.exists():
        return f"食物图片不存在（ID：{food_id}）"
    return MessageSegment.text(caption + "\n") + MessageSegment.image(filepath.read_bytes())


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
    msg = _build_food_message(group_id, chosen_id, index)
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
    count = max(1, min(7, count))

    index = _load_index(group_id)
    if not index:
        await feast_cmd.finish(
            "本群还没有收集任何食物，使用 /收集食物 [名字] [图片] 来添加吧"
        )

    ids = [sid for sid, e in index.items() if not e.get("hidden")]
    if not ids:
        await feast_cmd.finish(
            "本群没有普通食物，吃大餐仅从普通食物中抽取（隐藏食物仅能从『吃什么』单抽获得）"
        )
    if len(ids) < count:
        await feast_cmd.finish(
            f"本群目前只有 {len(ids)} 道菜，无法凑齐 {count} 道，试试 /吃大餐 {len(ids)}"
        )

    chosen_ids = random.sample(ids, count)
    images_dir = _get_images_dir(group_id)
    parts: list[MessageSegment] = []
    for i, short_id in enumerate(chosen_ids, 1):
        entry = index[short_id]
        name = entry.get("name") or None
        label = _format_food_label(short_id, name)
        ordinal = "第一道菜" if i == 1 else f"第{i}道菜"
        text_seg = MessageSegment.text(f"{ordinal}：{label}\n")
        parts.append(text_seg)
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = images_dir / fn
        if filepath.exists():
            parts.append(MessageSegment.image(filepath.read_bytes()))
    await feast_cmd.finish(Message(parts))


async def _handle_what_to_eat(bot: Bot, event: GroupMessageEvent) -> None:
    setattr(event, "_yiyin_skip_repetition", True)
    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        return

    normal_ids = [sid for sid, e in index.items() if not e.get("hidden")]
    hidden_ids = _get_hidden_food_ids(index)

    triggered_hidden = False
    if hidden_ids:
        prob = _load_hidden_prob(group_id)
        if random.randint(1, 100) <= prob:
            triggered_hidden = True
            _save_hidden_prob(group_id, 3)
        else:
            _save_hidden_prob(group_id, min(100, prob + 1))

    if triggered_hidden and hidden_ids:
        short_id, index_changed = _pick_hidden_food_for_random(index)
        if not short_id:
            return
        if index_changed:
            _save_index(group_id, index)
        entry = index[short_id]
        name = entry.get("name") or short_id
        food_name = name.strip() if name and name.strip() else short_id
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = _get_images_dir(group_id) / fn
        if not filepath.exists():
            return

        await bot.send(event, "是啊，吃什么")
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
                    msg_id = img_resp.get("message_id") or (img_resp.get("data") or {}).get("message_id")
                if msg_id is not None:
                    await bot.call_api("delete_msg", message_id=msg_id)
            except Exception as e:
                logger.warning(f"撤回隐藏食物图片失败: {e}")

        asyncio.create_task(_recall_image())
        return

    ids = normal_ids if normal_ids else list(index.keys())
    short_id = random.choice(ids)
    entry = index[short_id]
    name = entry.get("name") or None
    label = _format_food_label(short_id, name)
    fn = entry.get("filename") or f"{short_id}.png"
    filepath = _get_images_dir(group_id) / fn
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
