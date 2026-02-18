"""
NoneBot2 角色扮演插件（37 — 重返未来：1999）
- @机器人 时必定回复
- 未被 @ 时以低概率随机参与群聊
- 默认关闭，需通过 /启用 角色扮演 开启
"""

import json
import random
import time
from collections import defaultdict, deque
from pathlib import Path

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.rule import Rule

from yiyin.llmapi import chat_completion
from yiyin.toggle import is_feature_enabled

# ==================== 路径与配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_PATH = Path(__file__).resolve().parent / "prompt.txt"
CONFIG_PATH = PROJECT_ROOT / "config" / "roleplay.json"

SYSTEM_PROMPT: str = PROMPT_PATH.read_text(encoding="utf-8").strip()

with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
    _config: dict = json.load(_f)

MODEL: str = _config.get("model", "claude-haiku-4-5-20251001")
REPLY_PROBABILITY: float = _config.get("reply_probability", 0.03)
MAX_CONTEXT: int = _config.get("max_context_messages", 30)
COOLDOWN: int = _config.get("cooldown_seconds", 300)
MAX_REPLY_TOKENS: int = _config.get("max_reply_tokens", 150)
TEMPERATURE: float = _config.get("temperature", 0.85)

# ==================== 运行时状态 ====================
# 每个群的消息历史记录：deque of {"role": str, "name": str, "content": str}
_group_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_CONTEXT))
# 每个群上次随机回复的时间戳
_last_reply_time: dict[int, float] = defaultdict(float)
# 机器人自身 QQ 号，启动后填充
_self_id: str = ""


@get_driver().on_bot_connect
async def _on_connect(bot: Bot):
    global _self_id
    _self_id = str(bot.self_id)


# ==================== 工具函数 ====================
def _is_at_me(event: GroupMessageEvent) -> bool:
    """检查消息是否 @了机器人"""
    if not _self_id:
        return False
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == _self_id:
            return True
    return False


def _extract_text(event: GroupMessageEvent) -> str:
    """提取消息的纯文本内容，去掉 @ 段"""
    parts: list[str] = []
    for seg in event.message:
        if seg.type == "text":
            parts.append(str(seg.data.get("text", "")))
    return "".join(parts).strip()


def _get_display_name(event: GroupMessageEvent) -> str:
    """获取发言者的群昵称或QQ昵称"""
    sender = event.sender
    return sender.card or sender.nickname or str(event.user_id)


async def _should_reply(event: GroupMessageEvent) -> bool:
    """判断是否应该回复这条消息"""
    group_id = event.group_id

    if not is_feature_enabled("roleplay", str(group_id)):
        return False

    if _is_at_me(event):
        return True

    now = time.time()
    if now - _last_reply_time[group_id] < COOLDOWN:
        return False

    return random.random() < REPLY_PROBABILITY


def _build_messages(group_id: int, current_text: str, sender_name: str) -> list[dict]:
    """构建发送给 LLM 的消息列表"""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    for msg in _group_history[group_id]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    content = f"{sender_name}：{current_text}" if current_text else f"{sender_name} 发送了一条消息"
    messages.append({"role": "user", "content": content})

    return messages


# ==================== 消息匹配规则 ====================
async def _roleplay_rule(event: GroupMessageEvent) -> bool:
    """只处理启用了角色扮演的群的消息"""
    return is_feature_enabled("roleplay", str(event.group_id))


roleplay_matcher = on_message(rule=Rule(_roleplay_rule), priority=99, block=False)


@roleplay_matcher.handle()
async def handle_group_msg(bot: Bot, event: GroupMessageEvent):
    """处理群消息：记录上下文，根据条件决定是否回复"""
    group_id = event.group_id
    sender_name = _get_display_name(event)
    text = _extract_text(event)

    # 忽略自己发的消息
    if str(event.user_id) == _self_id:
        return

    # 记录这条消息到群历史
    _group_history[group_id].append({
        "role": "user",
        "content": f"{sender_name}：{text}" if text else f"{sender_name} 发送了一条消息",
    })

    # 判断是否回复
    at_me = _is_at_me(event)

    if not at_me:
        now = time.time()
        if now - _last_reply_time[group_id] < COOLDOWN:
            return
        if random.random() >= REPLY_PROBABILITY:
            return

    # 构建并调用 LLM
    messages = _build_messages(group_id, text, sender_name)

    reply = await chat_completion(
        messages,
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_REPLY_TOKENS,
    )

    if not reply:
        if at_me:
            reply = "唔……脑子里的证明过程断了，等一下"
        else:
            return

    reply = reply.strip().strip('"').strip("'")

    # 记录自己的回复到历史
    _group_history[group_id].append({
        "role": "assistant",
        "content": reply,
    })
    _last_reply_time[group_id] = time.time()

    # 被 @ 时回复并 @ 回去，随机回复则不 @
    if at_me:
        await bot.send(event, MessageSegment.at(event.user_id) + " " + reply)
    else:
        await bot.send(event, reply)
