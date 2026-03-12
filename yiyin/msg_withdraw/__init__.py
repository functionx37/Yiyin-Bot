"""
NoneBot2 撤回插件
- 当群主、群管理或超级管理员对 bot 的消息贴上 id 100 的表情（糗大了）时，bot 尝试撤回该消息（不发道歉）
- 若为自动食物收集的食物添加消息，会同步删除对应食物记录并通知（撤回失败也会执行）
- 依赖 NapCat 等协议端上报 msg_emoji_like / group_msg_emoji_like 通知事件
"""

import re
from typing import Any, Literal, Optional, Union

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.adapters.onebot.v11.event import NoticeEvent
from nonebot.log import logger
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from yiyin.food import delete_food

# ==================== 自定义事件模型 ====================
# NapCat 上报 group_msg_emoji_like（群消息贴表情），结构为 likes: [{emoji_id, count}]
# 部分协议端可能上报 msg_emoji_like（单 emoji_id 字段），两者都支持


class MsgEmojiLikeNoticeEvent(NoticeEvent):
    """消息被贴表情通知事件（msg_emoji_like，单 emoji_id 格式）"""

    notice_type: Literal["msg_emoji_like"]
    user_id: int
    message_id: int
    emoji_id: str
    group_id: Optional[int] = None

    def get_user_id(self) -> str:
        return str(self.user_id)

    def get_session_id(self) -> str:
        if self.group_id:
            return f"group_{self.group_id}_{self.user_id}"
        return str(self.user_id)


class GroupMsgEmojiLikeNoticeEvent(NoticeEvent):
    """群消息贴表情通知事件（group_msg_emoji_like，NapCat 格式 likes 数组）"""

    notice_type: Literal["group_msg_emoji_like"]
    user_id: int
    message_id: int
    group_id: int
    likes: list[dict[str, Any]]  # [{"emoji_id": "424", "count": 1}]
    is_add: bool = True

    @property
    def emoji_id(self) -> str:
        """从 likes 中取第一个表情 ID（贴表情时通常只有一个）"""
        if self.likes and self.is_add:
            return str(self.likes[0].get("emoji_id", ""))
        return ""

    def get_user_id(self) -> str:
        return str(self.user_id)

    def get_session_id(self) -> str:
        return f"group_{self.group_id}_{self.user_id}"


# 注册自定义事件模型
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

OneBotV11Adapter.add_custom_model(MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)

# ==================== 规则 ====================

_APOLOGY_EMOJI_ID = "100"  # 糗大了

_FOOD_ID_RE = re.compile(r"食物ID[：:]\s*([A-Za-z0-9]+)")


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
    """从消息文本中解析食物 ID（自动食物收集添加食物时的格式）"""
    m = _FOOD_ID_RE.search(text)
    return m.group(1) if m else None


def _is_msg_emoji_like(event: NoticeEvent) -> bool:
    """是否为消息被贴表情通知（含 msg_emoji_like 与 group_msg_emoji_like）"""
    return isinstance(
        event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)
    )


def _is_apology_emoji(event: NoticeEvent) -> bool:
    """是否为糗大了表情（id 100），仅此表情才触发撤回"""
    if not isinstance(
        event, (MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent)
    ):
        return False
    return event.emoji_id == _APOLOGY_EMOJI_ID


# ==================== 注册 ====================
withdraw_matcher = on_notice(
    Rule(_is_msg_emoji_like, _is_apology_emoji),
    priority=5,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)


@withdraw_matcher.handle()
async def handle_msg_withdraw(
    bot: Bot,
    event: Union[MsgEmojiLikeNoticeEvent, GroupMsgEmojiLikeNoticeEvent],
):
    """群主/群管理/超级管理员对 bot 消息贴 id100 表情（糗大了）时：仅尝试撤回该消息"""
    # Rule 已保证 emoji_id==100；仅处理群消息（需 group_id 用于 delete_food 和 send）
    if not event.group_id:
        return

    bot_msg_id = event.message_id
    group_id = str(event.group_id)

    # 确认被贴表情的消息来自 bot
    try:
        msg_data = await bot.get_msg(message_id=bot_msg_id)
        sender_id = msg_data.get("sender", {}).get("user_id")
        if str(sender_id) != str(bot.self_id):
            return
    except Exception:
        return

    # 若为自动食物收集的食物添加消息，先删除对应食物记录
    deleted_food_id: str | None = None
    text = await _extract_recalled_text(bot, bot_msg_id)
    food_id = _parse_food_id(text)
    if food_id:
        if delete_food(group_id, food_id):
            deleted_food_id = food_id

    # 仅尝试撤回，超时失败也不影响后续操作
    try:
        await bot.call_api("delete_msg", message_id=bot_msg_id)
    except Exception as e:
        logger.warning(f"撤回消息失败（可能超时）: {e}")

    # 若删除了食物记录，发送通知（无论撤回是否成功）
    if deleted_food_id:
        try:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=f"已删除对应食物记录（ID：{deleted_food_id}）",
            )
        except Exception as e:
            logger.warning(f"发送食物删除通知失败: {e}")
