"""
NoneBot2 贴表情 / 发表情插件
- 命令：/贴表情列表            — 以合并转发消息形式展示所有可用表情
- 命令：/贴 <ID/含义/emoji> [引用] — 给引用的消息贴上指定表情
- 命令：/贴<数字>个 [引用]      — 给引用的消息随机贴上指定个数的表情
- 命令：/发 <ID/含义>          — 发送对应的QQ系统表情
- 命令：/发 随机               — 随机发送一个QQ系统表情
"""

import asyncio
import json
import random
import re
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
CONFIG_PATH = PROJECT_ROOT / "config" / "emoji_reaction.json"

# ==================== 加载表情数据 ====================
with open(EMOJI_JSON_PATH, "r", encoding="utf-8") as f:
    EMOJI_LIST: list[dict] = json.load(f)

_BY_ID: dict[str, dict] = {e["id"]: e for e in EMOJI_LIST}
_BY_NAME: dict[str, dict] = {e["name"]: e for e in EMOJI_LIST}
_BY_EMOJI: dict[str, dict] = {e["emoji"]: e for e in EMOJI_LIST if "emoji" in e}


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ==================== 注册命令 ====================
list_cmd = on_command("贴表情列表", priority=10, block=True)
stick_cmd = on_command("贴", priority=10, block=True)
send_cmd = on_command("发", priority=10, block=True)

_RANDOM_RE = re.compile(r"^(\d+)个$")


def _resolve_emoji(text: str) -> str | None:
    """根据用户输入解析出 emoji_id；支持已收录的 id/含义/emoji 和任意纯数字 ID"""
    if text in _BY_NAME:
        return _BY_NAME[text]["id"]
    if text in _BY_EMOJI:
        return _BY_EMOJI[text]["id"]
    if text in _BY_ID:
        return _BY_ID[text]["id"]
    if text.isdigit():
        return text
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

    nodes.append(_make_node(bot_name, bot_uin, (
        "「贴表情」可用表情一览\n"
        "━━━━━━━━━━━━━━━\n"
        "用法：\n"
        "  /贴 <ID/含义/emoji> [引用消息]\n"
        "  /贴<数字>个 [引用消息]  → 随机贴N个\n"
        "━━━━━━━━━━━━━━━\n"
        "未收录的ID也可以直接用 /贴 <ID> 尝试\n"
        "━━━━━━━━━━━━━━━\n"
        f"已收录 {len(EMOJI_LIST)} 个表情 "
        f"(QQ系统: {len(qq_emojis)}, Emoji: {len(unicode_emojis)})"
    )))

    for i in range(0, len(qq_emojis), CHUNK_SIZE):
        chunk = qq_emojis[i:i + CHUNK_SIZE]
        header = f"📦 QQ系统表情 ({i + 1}-{i + len(chunk)})"
        lines = [header, ""]
        lines.extend(_format_entry(e) for e in chunk)
        nodes.append(_make_node(bot_name, bot_uin, "\n".join(lines)))

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
    """统一处理 /贴N个 和 /贴<表情>"""
    target_msg_id = event.reply.message_id if event.reply else event.message_id
    text = args.extract_plain_text().strip()
    if not text:
        return

    # 优先匹配 "N个" → 随机贴
    m = _RANDOM_RE.match(text)
    if m:
        cfg = _load_config()
        max_random = cfg.get("max_random_count", 20)
        max_id = cfg.get("max_emoji_id", 470)
        count = min(int(m.group(1)), max_random)
        if count < 1:
            return
        ids = random.sample(range(1, max_id + 1), count)
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
        return

    # 指定表情
    emoji_id = _resolve_emoji(text)
    if not emoji_id:
        return

    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=target_msg_id,
            emoji_id=emoji_id,
        )
    except Exception:
        pass


# ==================== /发 ====================
@send_cmd.handle()
async def handle_send(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """发送QQ系统表情"""
    text = args.extract_plain_text().strip()
    if not text:
        return

    if text == "随机":
        cfg = _load_config()
        max_id = cfg.get("max_emoji_id", 470)
        face_id = random.randint(1, max_id)
        await send_cmd.finish(Message(MessageSegment.face(face_id)))
        return

    emoji_id = _resolve_emoji(text)
    if not emoji_id:
        return

    await send_cmd.finish(Message(MessageSegment.face(int(emoji_id))))


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
