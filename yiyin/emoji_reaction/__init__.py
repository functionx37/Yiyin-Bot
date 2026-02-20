"""
NoneBot2 贴表情插件
- 命令：/贴表情列表       — 以合并转发消息形式展示所有可用表情
- 命令：/贴 <表情> [引用]  — 给引用的消息贴上指定表情
- 命令：/贴 <数字> [引用]  — 给引用的消息随机贴上指定个数的表情
"""

import asyncio
import json
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EMOJI_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "emoji_reactions.json"

# ==================== 加载表情数据 ====================
with open(EMOJI_JSON_PATH, "r", encoding="utf-8") as f:
    EMOJI_LIST: list[dict] = json.load(f)

# 构建查找索引：按 id、name、emoji 查找
_BY_ID: dict[str, dict] = {e["id"]: e for e in EMOJI_LIST}
_BY_NAME: dict[str, dict] = {e["name"]: e for e in EMOJI_LIST}
_BY_EMOJI: dict[str, dict] = {e["emoji"]: e for e in EMOJI_LIST if "emoji" in e}

MAX_RANDOM_COUNT = 5

# ==================== 注册命令 ====================
list_cmd = on_command("贴表情列表", priority=10, block=True)
stick_cmd = on_command("贴", priority=10, block=True)


def _resolve_emoji(text: str) -> dict | None:
    """根据用户输入（id / 含义 / emoji 字符）查找对应的表情条目"""
    if text in _BY_ID:
        return _BY_ID[text]
    if text in _BY_NAME:
        return _BY_NAME[text]
    if text in _BY_EMOJI:
        return _BY_EMOJI[text]
    return None


def _format_entry(e: dict) -> str:
    """格式化单条表情显示文本"""
    emoji_char = e.get("emoji", "")
    tag = "Emoji" if e["type"] == 2 else "QQ"
    display = f"{emoji_char} " if emoji_char else ""
    return f"[{tag}] {display}{e['name']}  (ID: {e['id']})"


# ==================== /贴表情列表 ====================
@list_cmd.handle()
async def handle_emoji_list(bot: Bot, event: GroupMessageEvent):
    """以合并转发消息展示可用表情列表"""
    bot_info = await bot.get_login_info()
    bot_name = bot_info.get("nickname", "一印Bot")
    bot_uin = str(bot.self_id)

    qq_emojis = [e for e in EMOJI_LIST if e["type"] == 1]
    unicode_emojis = [e for e in EMOJI_LIST if e["type"] == 2]

    CHUNK_SIZE = 30
    nodes: list[dict] = []

    # 说明节点
    nodes.append(_make_node(bot_name, bot_uin, (
        "「贴表情」可用表情一览\n"
        "━━━━━━━━━━━━━━━\n"
        "用法：\n"
        "  /贴 <ID/含义/emoji> [引用消息]\n"
        "  /贴 <数字> [引用消息]  → 随机贴N个\n"
        "━━━━━━━━━━━━━━━\n"
        f"共 {len(EMOJI_LIST)} 个表情 "
        f"(QQ系统: {len(qq_emojis)}, Emoji: {len(unicode_emojis)})"
    )))

    # QQ 系统表情分页
    for i in range(0, len(qq_emojis), CHUNK_SIZE):
        chunk = qq_emojis[i:i + CHUNK_SIZE]
        header = f"📦 QQ系统表情 ({i + 1}-{i + len(chunk)})"
        lines = [header, ""]
        lines.extend(_format_entry(e) for e in chunk)
        nodes.append(_make_node(bot_name, bot_uin, "\n".join(lines)))

    # Emoji 表情分页
    for i in range(0, len(unicode_emojis), CHUNK_SIZE):
        chunk = unicode_emojis[i:i + CHUNK_SIZE]
        header = f"📦 Emoji表情 ({i + 1}-{i + len(chunk)})"
        lines = [header, ""]
        lines.extend(_format_entry(e) for e in chunk)
        nodes.append(_make_node(bot_name, bot_uin, "\n".join(lines)))

    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)


# ==================== /贴 ====================
@stick_cmd.handle()
async def handle_stick(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """给引用的消息贴表情"""
    if not event.reply:
        await stick_cmd.finish("请引用一条消息再使用此命令哦~")

    target_msg_id = event.reply.message_id
    text = args.extract_plain_text().strip()

    if not text:
        await stick_cmd.finish(
            "请指定要贴的表情，例如：\n"
            "  /贴 赞\n"
            "  /贴 76\n"
            "  /贴 👍\n"
            "  /贴 3  (随机贴3个)"
        )

    # 判断是否为纯数字且不是已知表情 ID → 随机贴 N 个
    if text.isdigit() and text not in _BY_ID:
        count = int(text)
        if count < 1:
            await stick_cmd.finish("数量至少为 1 哦~")
        if count > MAX_RANDOM_COUNT:
            await stick_cmd.finish(f"一次最多贴 {MAX_RANDOM_COUNT} 个表情~")

        chosen = random.sample(EMOJI_LIST, min(count, len(EMOJI_LIST)))
        success = []
        for emoji_entry in chosen:
            try:
                await bot.call_api(
                    "set_msg_emoji_like",
                    message_id=target_msg_id,
                    emoji_id=emoji_entry["id"],
                )
                success.append(emoji_entry)
            except Exception:
                pass
            await asyncio.sleep(0.3)

        if success:
            names = "、".join(
                (e.get("emoji", "") + e["name"]) for e in success
            )
            await stick_cmd.finish(f"已随机贴上 {len(success)} 个表情：{names}")
        else:
            await stick_cmd.finish("贴表情失败了，请稍后再试~")
        return

    # 指定表情
    entry = _resolve_emoji(text)
    if not entry:
        await stick_cmd.finish(
            f"找不到表情「{text}」，请使用 /贴表情列表 查看可用表情~"
        )

    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=target_msg_id,
            emoji_id=entry["id"],
        )
    except Exception as e:
        await stick_cmd.finish(f"贴表情失败：{e}")

    display = entry.get("emoji", "") + entry["name"]
    await stick_cmd.finish(f"已贴上「{display}」~")


# ==================== 工具函数 ====================
def _make_node(name: str, uin: str, text: str) -> dict:
    """构造合并转发消息节点"""
    return {
        "type": "node",
        "data": {
            "name": name,
            "uin": uin,
            "content": Message(MessageSegment.text(text)),
        },
    }
