"""
NoneBot2 角色扮演插件（37 — 重返未来：1999）
- @机器人 时必定回复
- 未被 @ 时以低概率随机参与群聊
- 默认关闭，需通过 /启用 角色扮演 开启
"""

import asyncio
import json
import os
import random
import re
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
DOCS_37_DIR = PROJECT_ROOT / "assets" / "documents" / "37"

_DEFAULTS = {
    "model": "claude-haiku-4-5-20251001",
    "reply_probability": 0.03,
    "max_context_messages": 30,
    "cooldown_seconds": 300,
    "max_reply_tokens": 512,
    "temperature": 0.85,
    "split_delay_min": 0.5,
    "split_delay_max": 2.0,
}

_config_cache: dict | None = None
_config_mtime: float = 0
_prompt_cache: str = ""
_prompt_mtime: float = 0
_37_excerpts_mtime: float = 0


def _load_config() -> dict:
    """读取配置文件，文件变更后自动重新加载"""
    global _config_cache, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return _config_cache or dict(_DEFAULTS)
    if _config_cache is None or mtime != _config_mtime:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
        _config_mtime = mtime
    return _config_cache


def _load_37_excerpts() -> str:
    """从 assets/documents/37 加载 37 的剧情对白作为参考"""
    if not DOCS_37_DIR.exists():
        return ""
    lines: list[str] = []
    for f in sorted(DOCS_37_DIR.glob("*.txt")):
        try:
            text = f.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("37：") or line.startswith("37:"):
                    content = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
                    if content and len(content) < 80:
                        lines.append(content)
        except OSError:
            continue
    # 去重并取前 50 条作为参考
    unique = list(dict.fromkeys(lines))
    sample = unique[:50] if unique else []
    return "\n".join(f"- {s}" for s in sample) if sample else ""


def _load_prompt() -> str:
    """读取 prompt 文件，文件变更后自动重新加载，并附加 37 剧情参考"""
    global _prompt_cache, _prompt_mtime, _37_excerpts_mtime
    prompt_mtime = 0
    docs_mtime = 0
    try:
        prompt_mtime = os.path.getmtime(PROMPT_PATH)
    except OSError:
        pass
    if DOCS_37_DIR.exists():
        try:
            docs_mtime = max(
                (f.stat().st_mtime for f in DOCS_37_DIR.glob("*.txt")),
                default=0,
            )
        except OSError:
            pass
    if (
        not _prompt_cache
        or prompt_mtime != _prompt_mtime
        or docs_mtime != _37_excerpts_mtime
    ):
        base = PROMPT_PATH.read_text(encoding="utf-8").strip()
        excerpts = _load_37_excerpts()
        if excerpts:
            base += f"\n\n【剧情参考例句】（说话时可参考以下风格）\n{excerpts}"
        _prompt_cache = base
        _prompt_mtime = prompt_mtime
        _37_excerpts_mtime = docs_mtime
    return _prompt_cache


def _cfg(key: str):
    """获取配置项，带默认值兜底"""
    return _load_config().get(key, _DEFAULTS.get(key))


def _is_superuser(user_id: str) -> bool:
    """判断用户是否为超级管理员"""
    try:
        su = get_driver().config.superusers
        return user_id in (su or set())
    except Exception:
        return False

# ==================== 运行时状态 ====================
# 每个群的消息历史记录：deque of {"role": str, "name": str, "content": str}
_group_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=200))
# 每个群上次随机回复的时间戳
_last_reply_time: dict[int, float] = defaultdict(float)
# 机器人自身 QQ 号，启动后填充
_self_id: str = ""


@get_driver().on_bot_connect
async def _on_connect(bot: Bot):
    global _self_id
    _self_id = str(bot.self_id)


# ==================== 工具函数 ====================
def _extract_text(event: GroupMessageEvent) -> str:
    """提取消息的纯文本内容"""
    return event.get_plaintext().strip()


def _get_display_name(event: GroupMessageEvent) -> str:
    """获取发言者的群昵称或QQ昵称"""
    sender = event.sender
    return sender.card or sender.nickname or str(event.user_id)


async def _should_reply(event: GroupMessageEvent) -> bool:
    """判断是否应该回复这条消息"""
    group_id = event.group_id

    if not is_feature_enabled("roleplay", str(group_id)):
        return False

    if event.to_me:
        return True

    now = time.time()
    if now - _last_reply_time[group_id] < _cfg("cooldown_seconds"):
        return False

    return random.random() < _cfg("reply_probability")


def _format_user_content(sender_name: str, text: str, is_superuser: bool) -> str:
    """格式化用户消息内容，超级管理员加特殊标记"""
    prefix = "[超级管理员] " if is_superuser else ""
    if text:
        return f"{prefix}{sender_name}：{text}"
    return f"{prefix}{sender_name} 发送了一条消息"


def _build_messages(
    group_id: int,
    current_text: str,
    sender_name: str,
    is_superuser: bool,
) -> list[dict]:
    """构建发送给 LLM 的消息列表"""
    messages: list[dict[str, str]] = [{"role": "system", "content": _load_prompt()}]

    max_ctx = _cfg("max_context_messages")
    history = list(_group_history[group_id])[-max_ctx:]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    content = _format_user_content(sender_name, current_text, is_superuser)
    messages.append({"role": "user", "content": content})

    return messages


def _split_reply(text: str) -> list[str]:
    """按句号、问号、感叹号等标点分段，返回非空片段列表"""
    if not text or not text.strip():
        return []
    # 按 。！？；… 等标点分割，保留分隔符在上一句末尾
    parts = re.split(r"([。！？；…])", text.strip())
    result: list[str] = []
    buf = ""
    for i, p in enumerate(parts):
        if p in "。！？；…":
            buf += p
            s = buf.strip()
            if s:
                result.append(s)
            buf = ""
        else:
            buf += p
    if buf.strip():
        result.append(buf.strip())
    return result


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
    is_superuser = _is_superuser(str(event.user_id))

    # 忽略自己发的消息
    if str(event.user_id) == _self_id:
        return

    # 记录这条消息到群历史（超级管理员加特殊标记）
    _group_history[group_id].append({
        "role": "user",
        "content": _format_user_content(sender_name, text, is_superuser),
    })

    # 判断是否回复
    at_me = event.to_me

    if not at_me:
        now = time.time()
        if now - _last_reply_time[group_id] < _cfg("cooldown_seconds"):
            return
        if random.random() >= _cfg("reply_probability"):
            return

    # 构建并调用 LLM
    messages = _build_messages(group_id, text, sender_name, is_superuser)

    reply = await chat_completion(
        messages,
        model=_cfg("model"),
        temperature=_cfg("temperature"),
        max_tokens=_cfg("max_reply_tokens"),
    )

    if not reply:
        if at_me:
            reply = "唔……脑子里的证明过程断了，等一下"
        else:
            return

    reply = reply.strip().strip('"').strip("'")

    # 记录自己的回复到历史（完整内容）
    _group_history[group_id].append({
        "role": "assistant",
        "content": reply,
    })
    _last_reply_time[group_id] = time.time()

    # 按标点分段逐条发送，每条之间随机延迟
    segments = _split_reply(reply)
    delay_min = _cfg("split_delay_min")
    delay_max = _cfg("split_delay_max")

    if len(segments) <= 1:
        # 无需分段，直接发送
        if at_me:
            await bot.send(event, MessageSegment.at(event.user_id) + " " + reply)
        else:
            await bot.send(event, reply)
    else:
        for i, seg in enumerate(segments):
            if i > 0:
                await asyncio.sleep(random.uniform(delay_min, delay_max))
            if at_me and i == 0:
                await bot.send(event, MessageSegment.at(event.user_id) + " " + seg)
            else:
                await bot.send(event, seg)
