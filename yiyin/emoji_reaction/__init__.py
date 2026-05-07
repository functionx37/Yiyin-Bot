"""
NoneBot2 贴表情 / 发表情插件
- 命令：/贴表情列表                — 发送使用方法和部分表情预览
- 命令：/贴 <ID/别名> [引用]       — 给引用的消息贴上指定表情
- 命令：/贴<数字>个 [引用]          — 给引用的消息随机贴上指定个数的表情
- 命令：/贴 <起始ID~结束ID> [引用]   — 给引用的消息依次贴上区间内所有表情
- 命令：/发 <ID/别名>              — 发送对应ID的QQ系统表情
- 命令：/发 随机                   — 随机发送一个QQ系统表情
- 命令：/贴表情别名 <ID> <别名>     — 绑定表情别名
- 命令：/贴表情新增 <ID.../起~止>   — 新增随机池（仅超级管理员）
- 命令：/贴表情移除 <ID.../起~止>   — 从随机池移除（仅超级管理员）
- 通知：自动拾取群消息贴表情事件中的 emoji_id，补充到随机池
- 通知：任意群友对群消息贴表情 id 128514 或 182 时，机器人对该消息贴 id 387
  （依赖协议端上报 msg_emoji_like / group_msg_emoji_like，事件模型见 msg_withdraw）
"""

import asyncio
import json
import os
import random
import re
from pathlib import Path
from typing import Any, Union

from nonebot import on_command, on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import NoticeEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from nonebot import get_driver

from yiyin.food import save_foods_from_image_urls
from yiyin.msg_withdraw import GroupMsgEmojiLikeNoticeEvent, MsgEmojiLikeNoticeEvent
from yiyin.toggle import is_feature_enabled_async

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "emoji_reaction.json"
HELP_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "help.json"
EMOJI_IMG_DIR = PROJECT_ROOT / "assets" / "images" / "emoji_list"

MAX_RANDOM_COUNT = 20
_RANDOM_RE = re.compile(r"^(\d+)个$")
_ID_RANGE_RE = re.compile(r"^(\d+)\s*[~～]\s*(\d+)$")


# ==================== 工具函数 ====================
def _default_config() -> dict[str, Any]:
    return {"interval": [], "add": [], "remove": [], "alias": {}}


def _normalize_interval(raw: Any) -> list[list[int]]:
    intervals: list[list[int]] = []
    if not isinstance(raw, list):
        return intervals
    seen: set[tuple[int, int]] = set()
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(v, int) for v in item)
        ):
            continue
        start, end = item
        if start > end:
            start, end = end, start
        if start < 0:
            continue
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        intervals.append([start, end])
    intervals.sort(key=lambda x: (x[0], x[1]))
    return intervals


def _normalize_int_list(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    values = {item for item in raw if isinstance(item, int) and item >= 0}
    return sorted(values)


def _normalize_alias_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    alias_map: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 0:
            continue
        alias = key.strip()
        if not alias:
            continue
        alias_map[alias] = value
    return dict(sorted(alias_map.items(), key=lambda item: item[0]))


def _normalize_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        return {
            "interval": _normalize_interval(raw),
            "add": [],
            "remove": [],
            "alias": {},
        }
    if not isinstance(raw, dict):
        return _default_config()
    return {
        "interval": _normalize_interval(raw.get("interval")),
        "add": _normalize_int_list(raw.get("add")),
        "remove": _normalize_int_list(raw.get("remove")),
        "alias": _normalize_alias_map(raw.get("alias")),
    }


def _load_config() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = _default_config()
    return _normalize_config(raw)


def _save_config(config: dict[str, Any]) -> None:
    config = _normalize_config(config)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def _build_pool(config: dict[str, Any]) -> list[int]:
    pool: set[int] = set()
    for start, end in config["interval"]:
        pool.update(range(start, end + 1))
    pool.update(config["add"])
    pool.difference_update(config["remove"])
    return sorted(pool)


def _random_from_pool(pool: list[int]) -> int:
    return random.choice(pool)


def _is_in_interval(config: dict[str, Any], emoji_id: int) -> bool:
    return any(start <= emoji_id <= end for start, end in config["interval"])


def _resolve_emoji_id(text: str, config: dict[str, Any]) -> int | None:
    if text.isdigit():
        return int(text)
    alias_id = config["alias"].get(text)
    if isinstance(alias_id, int):
        return alias_id
    return None


def _load_help_text() -> str:
    """从 help.json 读取贴表情模块的帮助信息"""
    with open(HELP_JSON_PATH, "r", encoding="utf-8") as f:
        help_data = json.load(f)
    for module in help_data:
        if module.get("module") == "贴表情":
            lines = [f"📦 {module['module']}"]
            for func in module.get("function", []):
                cmd = func.get("command", "")
                desc = func.get("description", "")
                lines.append(f"  {cmd}\n    {desc}")
            return "\n".join(lines)
    return "贴表情模块帮助信息未找到"


def _make_node(name: str, uin: str, content: Message) -> dict:
    return {
        "type": "node",
        "data": {"name": name, "uin": uin, "content": content},
    }


def _parse_id_specs(text: str) -> list[int] | None:
    ids: set[int] = set()
    tokens = text.split()
    if not tokens:
        return None
    for token in tokens:
        if token.isdigit():
            ids.add(int(token))
            continue
        m = _ID_RANGE_RE.match(token)
        if not m:
            return None
        start = int(m.group(1))
        end = int(m.group(2))
        if start > end:
            start, end = end, start
        ids.update(range(start, end + 1))
    return sorted(ids)


def _chunk_ids(ids: list[int], size: int = 5) -> str:
    lines = []
    for i in range(0, len(ids), size):
        lines.append(" ".join(str(eid) for eid in ids[i : i + size]))
    return "\n".join(lines)


def _apply_add_ids(config: dict[str, Any], ids: list[int]) -> tuple[list[int], list[int]]:
    added: list[int] = []
    already_enabled: list[int] = []
    for emoji_id in ids:
        if emoji_id in config["remove"]:
            config["remove"].remove(emoji_id)
        if _is_in_interval(config, emoji_id):
            already_enabled.append(emoji_id)
            continue
        if emoji_id in config["add"]:
            already_enabled.append(emoji_id)
            continue
        config["add"].append(emoji_id)
        added.append(emoji_id)
    config["add"] = sorted(set(config["add"]))
    config["remove"] = sorted(set(config["remove"]))
    return added, already_enabled


def _apply_remove_ids(config: dict[str, Any], ids: list[int]) -> tuple[list[int], list[int]]:
    removed: list[int] = []
    already_disabled: list[int] = []
    add_set = set(config["add"])
    remove_set = set(config["remove"])
    for emoji_id in ids:
        was_enabled = _is_in_interval(config, emoji_id) or emoji_id in add_set
        if emoji_id in add_set:
            add_set.remove(emoji_id)
        if emoji_id not in remove_set:
            remove_set.add(emoji_id)
        if was_enabled:
            removed.append(emoji_id)
        else:
            already_disabled.append(emoji_id)
    config["add"] = sorted(add_set)
    config["remove"] = sorted(remove_set)
    return removed, already_disabled


def _extract_notice_emoji_id(
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
) -> int | None:
    emoji_id = getattr(event, "emoji_id", "")
    if not isinstance(emoji_id, str) or not emoji_id.isdigit():
        return None
    return int(emoji_id)


def _is_msg_emoji_like(event: NoticeEvent) -> bool:
    return isinstance(event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent))


def _extract_hash_stick_text(event: GroupMessageEvent) -> str | None:
    """提取 #贴 命令后的参数文本。"""
    text = event.get_message().extract_plain_text().strip()
    m = re.fullmatch(r"#贴\s*(.*)", text)
    if not m:
        return None
    return m.group(1).strip()


def _is_hash_stick(event: GroupMessageEvent) -> bool:
    return _extract_hash_stick_text(event) is not None


def _is_notice_from_bot(event: NoticeEvent) -> bool:
    """是否为 bot 自己触发的贴表情事件。"""
    return str(getattr(event, "user_id", "")) == str(getattr(event, "self_id", ""))


async def _should_handle_auto_collect_emoji(event: NoticeEvent) -> bool:
    """仅在确实需要自动补充新表情 ID 时才进入 matcher。"""
    if not _is_msg_emoji_like(event):
        return False
    if _is_notice_from_bot(event):
        return False

    emoji_id = _extract_notice_emoji_id(event)
    if emoji_id is None:
        return False

    config = _load_config()
    if _is_in_interval(config, emoji_id):
        return False
    if emoji_id in config["add"] or emoji_id in config["remove"]:
        return False

    setattr(event, "_yiyin_auto_collect_emoji_id", emoji_id)
    return True


# ==================== 群友贴表情 → 机器人跟贴 387 ====================
_FEATURE_KEY = "yiyin.emoji_reaction"
_TRIGGER_EMOJI_IDS = frozenset({"128514", "182"})
_MIRROR_EMOJI_ID = "387"
_COLLECT_FOOD_EMOJI_ID = "127838"


def _is_mirror387_trigger(event: NoticeEvent) -> bool:
    """群消息被贴表情且为 128514 / 182（事件模型与 msg_withdraw 一致）"""
    if not isinstance(event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)):
        return False
    return event.emoji_id in _TRIGGER_EMOJI_IDS


def _is_superuser_user_id(user_id: str) -> bool:
    """判断用户是否为超级管理员。"""
    superusers = get_driver().config.superusers
    return user_id in {str(uid) for uid in superusers}


async def _emoji_reaction_enabled(
    bot: Bot,
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
) -> bool:
    """仅在当前群启用贴表情功能时处理贴表情通知。"""
    if not event.group_id:
        return False
    return await is_feature_enabled_async(bot, _FEATURE_KEY, str(event.group_id))


async def _extract_message_image_urls(bot: Bot, message_id: int) -> list[str]:
    """读取目标消息中的图片 URL。"""
    try:
        msg_data = await bot.get_msg(message_id=message_id)
    except Exception:
        return []

    raw_msg = msg_data.get("message", [])
    urls: list[str] = []
    if isinstance(raw_msg, Message):
        segments = raw_msg
    elif isinstance(raw_msg, str):
        segments = Message(raw_msg)
    elif isinstance(raw_msg, list):
        segments = []
        for seg in raw_msg:
            if isinstance(seg, MessageSegment):
                segments.append(seg)
            elif isinstance(seg, dict):
                segments.append(MessageSegment(seg.get("type", ""), seg.get("data", {})))
    else:
        return []

    for seg in segments:
        if seg.type != "image":
            continue
        url = seg.data.get("url")
        if url:
            urls.append(url)
    return urls


emoji_mirror387_notice = on_notice(
    Rule(_is_mirror387_trigger),
    priority=6,
    block=False,
)


@emoji_mirror387_notice.handle()
async def handle_emoji_mirror387(
    bot: Bot,
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
):
    """任意群友对群消息贴 128514 或 182 时，机器人对该消息贴 387"""
    if not await _emoji_reaction_enabled(bot, event):
        return
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id=_MIRROR_EMOJI_ID,
        )
    except Exception:
        pass


def _is_collect_food_trigger(event: NoticeEvent) -> bool:
    """超级管理员贴 127838 时，按收集食物处理。"""
    if not isinstance(event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)):
        return False
    if event.emoji_id != _COLLECT_FOOD_EMOJI_ID:
        return False
    return _is_superuser_user_id(str(event.user_id))


emoji_collect_food_notice = on_notice(
    Rule(_is_collect_food_trigger),
    priority=7,
    block=False,
)


@emoji_collect_food_notice.handle()
async def handle_emoji_collect_food(
    bot: Bot,
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
):
    """超级管理员给含图消息贴 127838 时，视为引用并收集食物。"""
    if not await _emoji_reaction_enabled(bot, event):
        return

    image_urls = await _extract_message_image_urls(bot, event.message_id)
    if not image_urls:
        return

    result = await save_foods_from_image_urls(
        str(event.group_id),
        image_urls,
        None,
        name_only_with_llm=True,
        log_prefix="贴表情收集食物自动命名",
    )
    if not result:
        return

    try:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.reply(event.message_id) + MessageSegment.text(result),
        )
    except Exception:
        pass


# ==================== 自动拾取贴表情 ID ====================
emoji_auto_collect_notice = on_notice(
    Rule(_should_handle_auto_collect_emoji),
    priority=60,
    block=False,
)


@emoji_auto_collect_notice.handle()
async def handle_auto_collect_emoji_id(
    bot: Bot,
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
):
    if not await _emoji_reaction_enabled(bot, event):
        return

    emoji_id = getattr(event, "_yiyin_auto_collect_emoji_id", None)
    if not isinstance(emoji_id, int):
        return

    config = _load_config()
    config["add"].append(emoji_id)
    _save_config(config)


# ==================== 注册命令 ====================
list_cmd = on_command("贴表情列表", priority=10, block=True)
stick_cmd = on_command("贴", priority=10, block=True)
hash_stick_matcher = on_message(Rule(_is_hash_stick), priority=10, block=True)
send_cmd = on_command("发", priority=10, block=True)
alias_cmd = on_command("贴表情别名", priority=10, block=True)
add_cmd = on_command("贴表情新增", priority=10, block=True, permission=SUPERUSER)
remove_cmd = on_command("贴表情移除", priority=10, block=True, permission=SUPERUSER)


# ==================== /贴表情列表 ====================
@list_cmd.handle()
async def handle_emoji_list(bot: Bot, event: GroupMessageEvent):
    bot_info = await bot.get_login_info()
    bot_name = bot_info.get("nickname", "一印Bot")
    bot_uin = str(bot.self_id)

    nodes: list[dict] = []

    # 第一条：help.json 中贴表情系列的使用方法
    help_text = _load_help_text()
    nodes.append(_make_node(bot_name, bot_uin, Message(MessageSegment.text(help_text))))

    # 第二条：表情预览图片
    preview_msg = Message(MessageSegment.text("以下为部分表情预览，仅供参考\n"))
    for img_file in sorted(EMOJI_IMG_DIR.glob("*.png")):
        preview_msg += MessageSegment.image(img_file.read_bytes())
    nodes.append(_make_node(bot_name, bot_uin, preview_msg))

    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)


# ==================== /贴 ====================
async def _do_stick(
    bot: Bot,
    event: GroupMessageEvent,
    text: str,
    *,
    silent: bool,
) -> None:
    target_msg_id = event.reply.message_id if event.reply else event.message_id
    if not text:
        return

    config = _load_config()

    # "N个" → 随机贴
    m = _RANDOM_RE.match(text)
    if m:
        pool = _build_pool(config)
        count = min(int(m.group(1)), MAX_RANDOM_COUNT, len(pool))
        if count < 1:
            return
        ids = random.sample(pool, count)
        for eid in ids:
            try:
                await bot.call_api(
                    "set_msg_emoji_like",
                    message_id=target_msg_id,
                    emoji_id=str(eid),
                )
            except Exception:
                pass
            await asyncio.sleep(0.3)
        if not silent:
            await stick_cmd.finish("贴了以下表情：\n" + _chunk_ids(ids))
        return

    # "A~B" → 顺序贴区间内所有表情
    m = _ID_RANGE_RE.match(text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        if start > end:
            start, end = end, start
        ids = list(range(start, end + 1))
        if len(ids) > MAX_RANDOM_COUNT:
            if not silent:
                await stick_cmd.finish(f"连续贴表情最多支持 {MAX_RANDOM_COUNT} 个 ID")
            return
        for eid in ids:
            try:
                await bot.call_api(
                    "set_msg_emoji_like",
                    message_id=target_msg_id,
                    emoji_id=str(eid),
                )
            except Exception:
                pass
            await asyncio.sleep(0.3)
        if not silent:
            await stick_cmd.finish("贴了以下表情：\n" + _chunk_ids(ids))
        return

    emoji_id = _resolve_emoji_id(text, config)
    if emoji_id is None:
        return
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=target_msg_id,
            emoji_id=str(emoji_id),
        )
    except Exception:
        pass


@stick_cmd.handle()
async def handle_stick(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    await _do_stick(bot, event, text, silent=False)


@hash_stick_matcher.handle()
async def handle_hash_stick(bot: Bot, event: GroupMessageEvent):
    text = _extract_hash_stick_text(event)
    if text is None:
        return
    await _do_stick(bot, event, text, silent=True)


# ==================== /发 ====================
@send_cmd.handle()
async def handle_send(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        return

    config = _load_config()

    if text == "随机":
        pool = _build_pool(config)
        if not pool:
            await send_cmd.finish("当前没有可用的随机表情 ID")
        for _ in range(50):
            face_id = _random_from_pool(pool)
            try:
                await bot.send(event, Message(MessageSegment.face(face_id)))
            except Exception:
                await asyncio.sleep(0.1)
                continue
            await send_cmd.finish(f"发送了表情 ID: {face_id}")
        await send_cmd.finish("随机发送失败，请稍后再试")
        return

    emoji_id = _resolve_emoji_id(text, config)
    if emoji_id is None:
        return
    try:
        await bot.send(event, Message(MessageSegment.face(emoji_id)))
    except Exception:
        await send_cmd.finish(f"发送失败，ID {emoji_id} 对应的表情不存在")


# ==================== /贴表情别名 ====================
@alias_cmd.handle()
async def handle_emoji_alias(args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        return
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit():
        return

    emoji_id = int(parts[0])
    alias = parts[1].strip()
    if not alias:
        return

    config = _load_config()
    config["alias"][alias] = emoji_id
    _save_config(config)
    await alias_cmd.finish(f"已将“{alias}”绑定到表情 ID: {emoji_id}")


# ==================== /贴表情新增 ====================
@add_cmd.handle()
async def handle_emoji_add(args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    ids = _parse_id_specs(text)
    if not ids:
        return

    config = _load_config()
    added, already_enabled = _apply_add_ids(config, ids)
    _save_config(config)

    parts: list[str] = []
    if added:
        parts.append("已新增以下表情 ID：\n" + _chunk_ids(added))
    if already_enabled:
        parts.append("以下表情 ID 原本已可用：\n" + _chunk_ids(already_enabled))
    if parts:
        await add_cmd.finish("\n\n".join(parts))


# ==================== /贴表情移除 ====================
@remove_cmd.handle()
async def handle_emoji_remove(args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    ids = _parse_id_specs(text)
    if not ids:
        return

    config = _load_config()
    removed, already_disabled = _apply_remove_ids(config, ids)
    _save_config(config)

    parts: list[str] = []
    if removed:
        parts.append("已移除以下表情 ID：\n" + _chunk_ids(removed))
    if already_disabled:
        parts.append("以下表情 ID 原本已不可用：\n" + _chunk_ids(already_disabled))
    if parts:
        await remove_cmd.finish("\n\n".join(parts))
