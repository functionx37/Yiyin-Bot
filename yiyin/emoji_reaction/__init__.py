"""
NoneBot2 贴表情 / 发表情插件
- 命令：/贴表情列表            — 发送使用方法和部分表情预览
- 命令：/贴 <ID> [引用]       — 给引用的消息贴上指定ID的表情
- 命令：/贴<数字>个 [引用]      — 给引用的消息随机贴上指定个数的表情
- 命令：/发 <ID>              — 发送对应ID的QQ系统表情
- 命令：/发 随机               — 随机发送一个QQ系统表情
- 通知：任意群友对群消息贴表情 id 128514 或 182 时，机器人对该消息贴 id 387
  （依赖协议端上报 msg_emoji_like / group_msg_emoji_like，事件模型见 msg_withdraw）
"""

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Union

from nonebot import on_command, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.event import NoticeEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from yiyin.msg_withdraw import GroupMsgEmojiLikeNoticeEvent, MsgEmojiLikeNoticeEvent

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "emoji_reaction.json"
HELP_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "help.json"
EMOJI_IMG_DIR = PROJECT_ROOT / "assets" / "images" / "emoji_list"

MAX_RANDOM_COUNT = 20
_RANDOM_RE = re.compile(r"^(\d+)个$")


# ==================== 工具函数 ====================
def _load_ranges() -> list[list[int]]:
    """加载随机范围配置，格式如 [[0, 470], [500, 600]]"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_pool(ranges: list[list[int]]) -> list[int]:
    """根据范围列表构建候选ID池"""
    pool: list[int] = []
    for r in ranges:
        pool.extend(range(r[0], r[1] + 1))
    return pool


def _random_from_pool(pool: list[int]) -> int:
    return random.choice(pool)


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


# ==================== 群友贴表情 → 机器人跟贴 387 ====================
_TRIGGER_EMOJI_IDS = frozenset({"128514", "182"})
_MIRROR_EMOJI_ID = "387"


def _is_mirror387_trigger(event: NoticeEvent) -> bool:
    """群消息被贴表情且为 128514 / 182（事件模型与 msg_withdraw 一致）"""
    if not isinstance(event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)):
        return False
    return event.emoji_id in _TRIGGER_EMOJI_IDS


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
    if not event.group_id:
        return
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=event.message_id,
            emoji_id=_MIRROR_EMOJI_ID,
        )
    except Exception:
        pass


# ==================== 注册命令 ====================
list_cmd = on_command("贴表情列表", priority=10, block=True)
stick_cmd = on_command("贴", priority=10, block=True)
send_cmd = on_command("发", priority=10, block=True)


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
@stick_cmd.handle()
async def handle_stick(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    target_msg_id = event.reply.message_id if event.reply else event.message_id
    text = args.extract_plain_text().strip()
    if not text:
        return

    # "N个" → 随机贴
    m = _RANDOM_RE.match(text)
    if m:
        pool = _build_pool(_load_ranges())
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
        # 列出贴的表情ID，一行五个
        lines = []
        for i in range(0, len(ids), 5):
            lines.append(" ".join(str(eid) for eid in ids[i : i + 5]))
        await stick_cmd.finish("贴了以下表情：\n" + "\n".join(lines))
        return

    # 指定ID
    if not text.isdigit():
        return
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=target_msg_id,
            emoji_id=text,
        )
    except Exception:
        pass


# ==================== /发 ====================
@send_cmd.handle()
async def handle_send(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        return

    if text == "随机":
        pool = _build_pool(_load_ranges())
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

    # 指定ID
    if not text.isdigit():
        return
    face_id = int(text)
    try:
        await bot.send(event, Message(MessageSegment.face(face_id)))
    except Exception:
        await send_cmd.finish(f"发送失败，ID {face_id} 对应的表情不存在")
