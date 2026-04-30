"""
NoneBot2 食物图鉴插件（按群隔离）
- 命令：/收集食物 [名字] [图片] — 保存食物图片，支持引用图片，可选名字
- 子模块 auto_collect：自动食物收集（常关，需 /启用 自动食物收集）
- 命令：/删除食物 <id/名字> — 仅超级管理员，支持引用食物消息自动提取 ID
- 命令：/补充名字 <id/名字> <新名字> — 支持引用食物消息后 /补充名字 新名字 自动提取 ID
- 命令：/标记 <id/名字> <tag> — 支持引用食物消息后 /标记 标签 自动提取 ID
- 命令：/吃 <id/名字/tag> — 支持引用食物消息自动提取 ID
- 命令：/隐藏 <id> — 将普通食物设为隐藏食物，支持引用食物消息自动提取 ID
- 命令：/吃大餐 [数量] — 默认三道菜，最多十道
- 触发：有人发『吃什么』时回复『是啊，吃什么』并随机一张图请你吃（单抽有概率触发隐藏食物）
"""

import asyncio
import json
import random
import re
import string
from pathlib import Path

import httpx
from nonebot import on_command, on_keyword, on_message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

from yiyin.food.llm_recognition import (
    recognize_food_from_image_bytes,
    suggest_food_name_from_image_bytes,
)

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "food"


def _get_group_dir(group_id: str) -> Path:
    """获取群组数据目录（按群隔离）"""
    return DATA_DIR / group_id


def _get_index_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "index.json"


def _get_images_dir(group_id: str) -> Path:
    return _get_group_dir(group_id) / "images"


def _get_hidden_prob_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_prob.json"


def _load_hidden_prob(group_id: str) -> int:
    """加载隐藏食物触发概率（1-100），默认 3"""
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
    """生成6位字母数字组合的唯一短ID"""
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str) -> dict[str, dict]:
    """加载索引 {short_id: {"name": str | None}}，图片文件名为 {short_id}.png"""
    index_file = _get_index_file(group_id)
    if not index_file.exists():
        return {}
    with open(index_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(group_id: str, index: dict[str, dict]) -> None:
    index_file = _get_index_file(group_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _add_to_index(group_id: str, name: str | None) -> str:
    """向索引添加一条，返回 short_id。图片需保存为 images/{short_id}.png"""
    index = _load_index(group_id)
    existing = set(index.keys())
    short_id = _generate_short_id(existing)
    index[short_id] = {"name": name}
    _save_index(group_id, index)
    return short_id


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    """从命令参数和引用消息中提取图片，支持多张图片（含引用）依次处理"""
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


# 从消息文本中解析食物 ID 的正则
_FOOD_ID_PREFIX_RE = re.compile(r"食物ID[：:]\s*([A-Za-z0-9]+(?:\s*[、]\s*[A-Za-z0-9]+)*)")
_FOOD_LABEL_RE = re.compile(r"『[^』]*』[（(]([A-Za-z0-9]+)[）)]")


def _parse_food_ids_from_text(text: str) -> list[str]:
    """从消息文本中解析食物 ID 列表。支持格式：食物ID：xxx、yyy；『name』（id）"""
    ids: list[str] = []
    # 1. 食物ID：Ab3x9K 或 食物ID：Ab3x9K、Bc4y2L
    m = _FOOD_ID_PREFIX_RE.search(text)
    if m:
        part = m.group(1)
        for sid in re.split(r"\s*[、]\s*", part):
            sid = sid.strip()
            if sid and sid not in ids:
                ids.append(sid)
    # 2. 『name』（id）格式
    for m in _FOOD_LABEL_RE.finditer(text):
        sid = m.group(1).strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


async def _extract_food_ids_from_reply(
    bot: Bot, event: GroupMessageEvent
) -> list[str]:
    """从引用的消息中提取食物 ID（一般为机器人发的食物相关消息）。无引用或解析失败返回空列表。"""
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
    """删除指定食物记录，供其他插件调用。成功返回 True，不存在返回 False。"""
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


def _format_food_label(short_id: str, name: str | None) -> str:
    """有名字：『name』（id）；无名字：『id』"""
    if name and name.strip():
        return f"『{name.strip()}』（{short_id}）"
    return f"『{short_id}』"


def _get_tags(entry: dict) -> list[str]:
    """获取食物的标签列表，兼容旧数据"""
    tags = entry.get("tags")
    if isinstance(tags, list):
        return [t for t in tags if isinstance(t, str) and t.strip()]
    return []


def _resolve_id_or_name(
    group_id: str, id_or_name: str, *, allow_dup: bool = False
) -> tuple[list[str] | None, str | None]:
    """
    根据 id 或名字解析出对应的食物 id 列表。
    - 若 id_or_name 是 index 中的 key，返回 ([id], None)
    - 若按名字匹配到唯一一条，返回 ([id], None)
    - 若按名字匹配到多条且 allow_dup=True，返回 (ids, None)
    - 若按名字匹配到多条且 allow_dup=False，返回 (None, 重名提示消息)
    - 若未找到，返回 (None, 不存在提示)
    """
    index = _load_index(group_id)
    if not id_or_name or not id_or_name.strip():
        return None, "请输入食物ID或名字"

    key = id_or_name.strip()

    # 1. 先按 id 查找
    if key in index:
        return [key], None

    # 2. 按名字查找
    matched: list[str] = []
    for sid, entry in index.items():
        name = entry.get("name")
        if name and str(name).strip() == key:
            matched.append(sid)

    if not matched:
        return None, f"未找到名为『{key}』或ID为『{key}』的食物"

    if len(matched) == 1:
        return matched, None

    # 重名
    if allow_dup:
        return matched, None
    ids_text = "\n".join(matched)
    return None, f"『{key}』对应的记录有：\n{ids_text}\n请使用id操作。"


def _get_foods_by_tag(group_id: str, tag: str) -> list[str]:
    """获取拥有指定标签的食物 id 列表（不含隐藏食物）"""
    index = _load_index(group_id)
    result: list[str] = []
    tag_clean = tag.strip() if tag else ""
    if not tag_clean:
        return result
    for sid, entry in index.items():
        if entry.get("hidden"):
            continue
        if tag_clean in _get_tags(entry):
            result.append(sid)
    return result


async def add_food_from_image_url(
    group_id: str, image_url: str, name: str | None
) -> str | None:
    """从图片 URL 保存食物到图鉴，供其他插件调用。成功返回提示消息，失败返回 None。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            content = resp.content
    except Exception:
        logger.exception(f"下载食物图片失败: {image_url}")
        return None

    index = _load_index(group_id)
    existing_ids = set(index.keys())
    short_id = _generate_short_id(existing_ids)
    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        filepath = images_dir / f"{short_id}.png"
        filepath.write_bytes(content)
        index[short_id] = {"name": name}
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存食物文件失败")
        filepath.unlink(missing_ok=True)
        return None

    name_hint = f"『{name}』" if name else "（未填名字，可用 /补充名字 <id> <名字> 补充）"
    return f"已保存 1 张食物图✓ {name_hint}\n食物ID：{short_id}"


def _extract_image_urls_from_segments(images: list[MessageSegment]) -> list[str]:
    """从图片消息段中提取可下载 URL。"""
    urls: list[str] = []
    for seg in images:
        if seg.type != "image":
            continue
        url = seg.data.get("url")
        if url:
            urls.append(url)
    return urls


async def _download_food_images(
    group_id: str, image_urls: list[str]
) -> list[tuple[str, bytes, str | None]] | None:
    """下载待收集图片，任一失败则返回 None。"""
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
                downloaded.append((short_id, resp.content, resp.headers.get("content-type")))
            except Exception:
                logger.exception(f"下载食物图片失败: {url}")
                return None
    return downloaded


async def _resolve_food_names(
    downloaded: list[tuple[str, bytes, str | None]],
    name: str | None,
    *,
    name_only_with_llm: bool,
    log_prefix: str,
) -> dict[str, str | None]:
    """为下载后的图片解析名称。"""
    resolved_names: dict[str, str | None] = {}
    if name:
        for short_id, _, _ in downloaded:
            resolved_names[short_id] = name
        return resolved_names

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
            resolved_names[short_id] = auto_name
        return resolved_names

    name_results = await asyncio.gather(
        *[
            recognize_food_from_image_bytes(
                content,
                content_type,
                log_prefix=log_prefix,
            )
            for _, content, content_type in downloaded
        ]
    )
    for (short_id, _, _), (rec_type, auto_name) in zip(downloaded, name_results):
        resolved_names[short_id] = auto_name if rec_type == "FOOD" else None
    return resolved_names


def _build_collect_food_success_message(
    downloaded: list[tuple[str, bytes, str | None]],
    resolved_names: dict[str, str | None],
    name: str | None,
) -> str:
    """构造成功收集食物的提示文本。"""
    id_str = "、".join(sid for sid, _, _ in downloaded)
    if name:
        return f"已保存 {len(downloaded)} 张食物图✓ 『{name}』\n食物ID：{id_str}"

    auto_named_count = sum(1 for n in resolved_names.values() if n)
    labels_text = "\n".join(
        _format_food_label(short_id, resolved_names.get(short_id))
        for short_id, _, _ in downloaded
    )
    if auto_named_count:
        name_hint = f"（已自动命名 {auto_named_count} 张，名称仅供参考，可用 /补充名字 <id> <名字> 调整）"
    else:
        name_hint = "（未指定名字，且自动命名失败，可用 /补充名字 <id> <名字> 补充）"
    return f"已保存 {len(downloaded)} 张食物图✓ {name_hint}\n{labels_text}\n食物ID：{id_str}"


async def save_foods_from_image_urls(
    group_id: str,
    image_urls: list[str],
    name: str | None = None,
    *,
    name_only_with_llm: bool = False,
    log_prefix: str = "收集食物自动命名",
) -> str | None:
    """从多个图片 URL 收集食物；任一下载或保存失败时返回 None。"""
    if not image_urls:
        return None

    downloaded = await _download_food_images(group_id, image_urls)
    if not downloaded:
        return None

    resolved_names = await _resolve_food_names(
        downloaded,
        name,
        name_only_with_llm=name_only_with_llm,
        log_prefix=log_prefix,
    )

    index = _load_index(group_id)
    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        for short_id, content, _ in downloaded:
            index[short_id] = {"name": resolved_names.get(short_id)}
            filepath = images_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存食物文件失败，回滚")
        for short_id, _, _ in downloaded:
            (images_dir / f"{short_id}.png").unlink(missing_ok=True)
        return None

    return _build_collect_food_success_message(downloaded, resolved_names, name)


# ==================== 注册命令 ====================
collect_food_cmd = on_command("收集食物", priority=10, block=True)


def _collect_food_image_first_rule(event: GroupMessageEvent) -> bool:
    """仅当消息首段为非文本（如图片）且包含收集食物命令时触发，用于支持『图片在上指令在下』"""
    msg = event.get_message()
    if not msg or msg[0].is_text():
        return False  # 首段是文本时由 on_command 处理
    text = msg.extract_plain_text().strip()
    if not text:
        return False
    if not re.search(r"[/.\!！]收集食物(?:\s|$)", text):
        return False
    has_image = any(seg.type == "image" for seg in msg)
    if not has_image and not (
        event.reply
        and event.reply.message
        and any(seg.type == "image" for seg in event.reply.message)
    ):
        return False
    return True


collect_food_image_first_cmd = on_message(
    _collect_food_image_first_rule, priority=9, block=True
)


delete_food_cmd = on_command(
    "删除食物", priority=10, block=True, permission=SUPERUSER
)
supplement_name_cmd = on_command("补充名字", priority=10, block=True)
hidden_food_cmd = on_command(
    "隐藏", priority=10, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER
)
feast_cmd = on_command("吃大餐", priority=10, block=True)
eat_cmd = on_command("吃", priority=10, block=True)
tag_food_cmd = on_command("标记", priority=10, block=True)

what_to_eat_matcher = on_keyword({"吃什么"}, priority=50, block=False)


# ==================== 命令处理 ====================
async def _do_collect_food(
    bot: Bot,
    event: GroupMessageEvent,
    name: str | None,
    args: Message,
    matcher: Matcher,
) -> None:
    """收集食物的核心逻辑，供 on_command 与 on_message（图片在上）共用"""
    images = await _extract_images(bot, event, args)
    if not images:
        await matcher.finish(
            "请在命令中附带图片或引用含图片的消息，例如：/收集食物 [名字] [图片]"
        )

    group_id = str(event.group_id)
    image_urls = _extract_image_urls_from_segments(images)
    if not image_urls:
        await matcher.finish("未获取到有效图片，请检查后重试")

    result = await save_foods_from_image_urls(
        group_id,
        image_urls,
        name,
        log_prefix="手动收集食物自动命名",
    )
    if not result:
        await matcher.finish("保存失败，已回滚（未修改数据），请稍后重试")
    await matcher.finish(result)


@collect_food_cmd.handle()
async def handle_collect_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /收集食物 [名字] [图片]：全部下载成功才写入，任一出错则回滚"""
    text = args.extract_plain_text().strip()
    name = text if text else None
    await _do_collect_food(bot, event, name, args, collect_food_cmd)


@collect_food_image_first_cmd.handle()
async def handle_collect_food_image_first(bot: Bot, event: GroupMessageEvent):
    """处理『图片在上、指令在下』的 /收集食物"""
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    m = re.search(r"[/.\!！]收集食物\s*(.*)", text)
    name_part = m.group(1).strip() if m and m.group(1) else ""
    name = name_part if name_part else None
    await _do_collect_food(bot, event, name, msg, collect_food_image_first_cmd)


@delete_food_cmd.handle()
async def handle_delete_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /删除食物 <id/名字>（仅超级管理员），支持引用食物消息自动提取 ID"""
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
    """处理 /补充名字 <id/名字> <新名字>，支持引用食物消息自动提取 ID"""
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    id_or_name: str | None = None
    new_name: str | None = None
    if len(parts) >= 2:
        id_or_name, new_name = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1 and event.reply:
        # 引用消息 + 仅一个参数 → 从引用提取 ID，参数作为新名字
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
    index[food_id]["name"] = new_name
    _save_index(group_id, index)
    await supplement_name_cmd.finish(f"已为食物（ID：{food_id}）补充名字『{new_name}』✓")


@hidden_food_cmd.handle()
async def handle_hidden_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /隐藏 <id>：将普通食物设为隐藏食物，支持引用食物消息自动提取 ID"""
    food_id = args.extract_plain_text().strip()
    if not food_id and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            food_id = reply_ids[0]
    if not food_id:
        await hidden_food_cmd.finish(
            "用法：/隐藏 <食物ID>，或引用食物消息后直接发送 /隐藏"
        )

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
    """处理 /标记 <id/名字> <tag>，支持引用食物消息自动提取 ID"""
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    id_or_name: str | None = None
    tag: str | None = None
    if len(parts) >= 2:
        id_or_name, tag = parts[0].strip(), parts[1].strip()
    elif len(parts) == 1 and event.reply:
        # 引用消息 + 仅一个参数 → 从引用提取 ID，参数作为标签
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            id_or_name, tag = reply_ids[0], parts[0].strip()
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
        entry["tags"] = tags
        _save_index(group_id, index)
    await tag_food_cmd.finish(f"已为食物（ID：{food_id}）添加标签『{tag}』✓")


@eat_cmd.handle()
async def handle_eat(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /吃 <id/名字/tag>：支持引用食物消息自动提取 ID"""
    text = args.extract_plain_text().strip()
    if not text and event.reply:
        reply_ids = await _extract_food_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            text = reply_ids[0]
        elif len(reply_ids) > 1:
            # 吃大餐等多条食物消息，取第一条
            text = reply_ids[0]
    # 兼容 `/吃什么` 被解析成 `/吃 什么` 的情况，直接走“吃什么”逻辑。
    if text == "什么":
        await _handle_what_to_eat(bot, event)
        await eat_cmd.finish()
    if not text:
        await eat_cmd.finish(
            "用法：/吃 <食物ID/名字/标签>，或引用食物消息后直接发送 /吃"
        )

    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        await eat_cmd.finish(
            "本群还没有收集任何食物，使用 /收集食物 [名字] [图片] 来添加吧"
        )

    # 1. 优先按 tag 判断：若存在该标签的食物，则随机吃一条
    tag_ids = _get_foods_by_tag(group_id, text)
    if tag_ids:
        short_id = random.choice(tag_ids)
        entry = index[short_id]
        name = entry.get("name") or None
        label = _format_food_label(short_id, name)
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = _get_images_dir(group_id) / fn
        if filepath.exists():
            msg = MessageSegment.text(f"请你吃{label}\n") + MessageSegment.image(
                filepath.read_bytes()
            )
            await eat_cmd.finish(msg)
        await eat_cmd.finish(f"食物图片不存在（ID：{short_id}）")

    # 2. 按 id/名字 查找，重名则都吃
    ids, err = _resolve_id_or_name(group_id, text, allow_dup=True)
    if err:
        await eat_cmd.finish(err)

    # 过滤掉隐藏食物（吃大餐逻辑也不吃隐藏的，这里保持一致）
    ids = [i for i in ids if not index.get(i, {}).get("hidden")]

    if not ids:
        await eat_cmd.finish("未找到可吃的食物")

    images_dir = _get_images_dir(group_id)
    parts: list[MessageSegment] = []
    if len(ids) == 1:
        short_id = ids[0]
        entry = index[short_id]
        name = entry.get("name") or None
        label = _format_food_label(short_id, name)
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = images_dir / fn
        if filepath.exists():
            msg = MessageSegment.text(f"请你吃{label}\n") + MessageSegment.image(
                filepath.read_bytes()
            )
            await eat_cmd.finish(msg)
        await eat_cmd.finish(f"食物图片不存在（ID：{short_id}）")

    for i, short_id in enumerate(ids, 1):
        entry = index[short_id]
        name = entry.get("name") or None
        label = _format_food_label(short_id, name)
        parts.append(MessageSegment.text(f"第{i}道：{label}\n"))
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = images_dir / fn
        if filepath.exists():
            parts.append(MessageSegment.image(filepath.read_bytes()))
    await eat_cmd.finish(Message(parts))


@feast_cmd.handle()
async def handle_feast(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /吃大餐 [数量]：第一道菜：name（id）【图片】... 默认3道，最多10道"""
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
    """处理“吃什么”入口：普通消息与 `/吃什么` 命令兼容共用。"""
    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        return

    # 分离普通食物与隐藏食物
    normal_ids = [sid for sid, e in index.items() if not e.get("hidden")]
    hidden_ids = [sid for sid, e in index.items() if e.get("hidden")]

    # 若有隐藏食物，按概率判定是否触发
    triggered_hidden = False
    if hidden_ids:
        prob = _load_hidden_prob(group_id)
        if random.randint(1, 100) <= prob:
            triggered_hidden = True
            _save_hidden_prob(group_id, 3)  # 抽中后概率重置为 3%
        else:
            _save_hidden_prob(group_id, min(100, prob + 1))  # 未中则 +1%

    if triggered_hidden and hidden_ids:
        # 从隐藏食物中抽取
        short_id = random.choice(hidden_ids)
        entry = index[short_id]
        name = entry.get("name") or short_id
        food_name = name.strip() if name and name.strip() else short_id
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = _get_images_dir(group_id) / fn
        if not filepath.exists():
            return

        await bot.send(event, "是啊，吃什么")
        user_id = event.get_user_id()
        text_msg = MessageSegment.text("恭喜") + MessageSegment.at(user_id) + MessageSegment.text(f"，请您享用{food_name}：")
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

    # 普通抽取：从普通食物中选（若无普通食物则从全部中选）
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
    msg = MessageSegment.text("请你吃") + MessageSegment.text(label) + MessageSegment.text("怎么样？\n") + MessageSegment.image(filepath.read_bytes())
    await bot.send(event, msg)


@what_to_eat_matcher.handle()
async def handle_what_to_eat(bot: Bot, event: GroupMessageEvent):
    """检测『吃什么』：是啊，吃什么 + 随机一张图 请你吃『name』（id）怎么样 [图片]；单抽有概率触发隐藏食物"""
    await _handle_what_to_eat(bot, event)
    await what_to_eat_matcher.finish()


from yiyin.food import auto_collect  # noqa: F401
