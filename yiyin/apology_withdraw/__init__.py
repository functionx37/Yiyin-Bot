"""
NoneBot2 道歉撤回插件
- 当管理/群主/超级管理员引用 bot 的消息并说「不行」时，bot 发一句道歉并撤回被引用的消息
- 若撤回的是图片识别的食物添加消息，会同步删除对应食物记录并通知
"""

import re

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

from yiyin.food import delete_food

# ==================== 规则 ====================

_FOOD_ID_RE = re.compile(r"食物ID[：:]\s*([A-Za-z0-9]+)")


def _has_reply_and_no(event: GroupMessageEvent) -> bool:
    """有引用消息且正文为「不行」"""
    if not event.reply:
        return False
    text = event.message.extract_plain_text().strip()
    return text == "不行"


async def _extract_recalled_text(bot: Bot, message_id: int) -> str:
    """获取指定消息的纯文本内容"""
    try:
        msg_data = await bot.get_msg(message_id=message_id)
        raw = msg_data.get("message", [])
        if isinstance(raw, str):
            return Message(raw).extract_plain_text()
        if isinstance(raw, list):
            return "".join(
                s.get("data", {}).get("text", "")
                for s in raw
                if isinstance(s, dict) and s.get("type") == "text"
            ).strip()
    except Exception:
        pass
    return ""


def _parse_food_id(text: str) -> str | None:
    """从消息文本中解析食物 ID（图片识别添加食物时的格式）"""
    m = _FOOD_ID_RE.search(text)
    return m.group(1) if m else None


# ==================== 注册 ====================
apology_matcher = on_message(
    Rule(_has_reply_and_no),
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)


@apology_matcher.handle()
async def handle_apology_withdraw(bot: Bot, event: GroupMessageEvent):
    """管理引用 bot 消息并说不行时：道歉并撤回 bot 的消息；若为食物添加消息则删除记录"""
    # 仅当引用的消息来自 bot 时处理（类型可能为 int/str，统一转 str 比较）
    if str(event.reply.sender.user_id) != str(bot.self_id):
        return

    bot_msg_id = event.reply.message_id
    group_id = str(event.group_id)

    # 若为图片识别的食物添加消息，先删除对应食物记录
    deleted_food_id: str | None = None
    text = await _extract_recalled_text(bot, bot_msg_id)
    food_id = _parse_food_id(text)
    if food_id:
        if delete_food(group_id, food_id):
            deleted_food_id = food_id

    try:
        await bot.send(event, "果咩纳塞！")
        await bot.call_api("delete_msg", message_id=bot_msg_id)
        if deleted_food_id:
            await bot.send(event, f"已删除对应食物记录（ID：{deleted_food_id}）")
    except Exception as e:
        logger.warning(f"道歉撤回失败: {e}")
