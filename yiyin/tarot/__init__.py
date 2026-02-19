"""
NoneBot2 塔罗牌插件
- 命令：/抽塔罗牌  — 随机抽取大阿卡纳塔罗牌（0-21），随机正位/逆位，发送图文消息
- 命令：/抽十连    — 一次性抽取 10 张塔罗牌，精简输出，每用户每天限用一次
- 命令：/占卜      — 引用抽十连结果，使用 AI 进行塔罗牌占卜解读
- 正位世界通知     — 抽到正位世界时 @群主 并发送 "世界！"（需启用「世界通知」功能）
"""

import json
import random
from datetime import date
from io import BytesIO
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg
from PIL import Image

from yiyin.llmapi import chat_completion

# ==================== 资源路径 ====================
# 项目根目录（yiyin/tarot/__init__.py -> 上两级为项目根目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TAROT_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "tarot.json"
TAROT_IMAGE_DIR = PROJECT_ROOT / "assets" / "images" / "tarot"

# ==================== 加载塔罗牌数据 ====================
with open(TAROT_JSON_PATH, "r", encoding="utf-8") as f:
    TAROT_DATA: list[dict] = json.load(f)

# 构建 id -> 卡牌数据 的映射，方便快速查询
TAROT_MAP: dict[int, dict] = {card["id"]: card for card in TAROT_DATA}

# ==================== 每日使用记录（抽十连） ====================
# key: user_id (str), value: 上次使用的日期
_ten_draw_usage: dict[str, date] = {}

# ==================== 世界牌 ID ====================
WORLD_CARD_ID = 21


async def _notify_world(bot: Bot, event: MessageEvent) -> None:
    """正位世界通知：若当前群启用了「世界通知」，则 @群主 并发送 "世界！" """
    if not isinstance(event, GroupMessageEvent):
        return

    from yiyin.toggle import is_feature_enabled

    group_id = str(event.group_id)
    if not is_feature_enabled("world_notify", group_id):
        return

    members = await bot.get_group_member_list(group_id=event.group_id)
    owner = next((m for m in members if m["role"] == "owner"), None)
    if owner:
        msg = MessageSegment.at(owner["user_id"]) + " 世界！"
        await bot.send(event, msg)


# ==================== 注册命令 ====================
tarot_cmd = on_command("抽塔罗牌", aliases=set(), priority=10, block=True)
tarot_ten_cmd = on_command("抽十连", aliases=set(), priority=10, block=True)


@tarot_cmd.handle()
async def handle_tarot(bot: Bot, event: MessageEvent):
    """处理 /抽塔罗牌 命令"""

    # 1. 随机抽取牌号（0-21）和方向（正位/逆位）
    card_id = random.randint(0, 21)
    is_upright = random.choice([True, False])

    # 2. 获取卡牌数据
    card = TAROT_MAP[card_id]
    name_zh = card["name_zh"]
    name_en = card["name_en"]
    orientation = "正位" if is_upright else "逆位"
    meaning = card["upright"] if is_upright else card["reversed"]

    # 3. 处理图片
    image_path = TAROT_IMAGE_DIR / f"{card_id}.png"
    img = Image.open(image_path)

    if not is_upright:
        # 逆位：旋转 180 度
        img = img.rotate(180)

    # 将图片写入内存缓冲区，转为 base64 发送
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    image_bytes = buf.getvalue()
    img.close()

    # 4. 构建消息
    # @调用者
    user_id = event.get_user_id()
    msg = MessageSegment.at(user_id) + " 抽到了：\n"
    # 牌名行
    msg += f"【{card_id}】{name_zh} （{name_en}）\n"
    # 图片
    msg += MessageSegment.image(image_bytes)
    # 寓意行
    msg += f"\n{orientation}：{meaning}"

    # 5. 发送消息
    await tarot_cmd.send(msg)

    # 6. 正位世界通知
    if card_id == WORLD_CARD_ID and is_upright:
        await _notify_world(bot, event)


@tarot_ten_cmd.handle()
async def handle_tarot_ten(bot: Bot, event: MessageEvent):
    """处理 /抽十连 命令：一次抽 10 张，精简输出，每用户每天限一次"""

    user_id = event.get_user_id()
    today = date.today()

    # 每日限制检查
    if _ten_draw_usage.get(user_id) == today:
        await tarot_ten_cmd.finish(
            MessageSegment.at(user_id) + " 你今天已经抽过十次了，明天再来吧~"
        )

    # 记录本次使用日期
    _ten_draw_usage[user_id] = today

    # 抽取 10 张牌（可重复）
    lines: list[str] = []
    has_upright_world = False
    for _ in range(10):
        card_id = random.randint(0, 21)
        is_upright = random.choice([True, False])
        card = TAROT_MAP[card_id]
        orientation = "正位" if is_upright else "逆位"
        lines.append(f"【{card['name_zh']}】（{card['name_en']}）{orientation}")
        if card_id == WORLD_CARD_ID and is_upright:
            has_upright_world = True

    result = "\n".join(lines)
    msg = MessageSegment.at(user_id) + " 十连抽结果：\n" + result

    await tarot_ten_cmd.send(msg)

    # 正位世界通知
    if has_upright_world:
        await _notify_world(bot, event)


# ==================== 占卜命令 ====================
_DIVINATION_PROMPT = (
    "你是一个资深塔罗牌占卜师"
    "用户会给你一组塔罗牌抽牌结果（十连抽），你需要根据牌面组合给出整体运势解读。\n"
    "要求：\n"
    "- 总共说3-5句话，不要分点、不要用标题\n"
    "- 综合所有牌面给一个整体解读\n"
    "- 可以点出一两张关键牌稍作展开\n"
    "- 绝对不要说'作为AI'之类的话"
)

divination_cmd = on_command("占卜", priority=10, block=True)


@divination_cmd.handle()
async def handle_divination(bot: Bot, event: MessageEvent):
    """处理 /占卜 命令：引用抽十连结果进行 AI 占卜"""
    user_id = event.get_user_id()

    # 提取引用消息中的牌面结果
    cards_text = ""
    if event.reply:
        reply_msg = event.reply.message
        cards_text = reply_msg.extract_plain_text().strip()

    if not cards_text:
        await divination_cmd.finish(
            MessageSegment.at(user_id) + " 请引用一条抽十连的结果来占卜哦~"
        )

    # 补充用户的提问（如有）
    from nonebot.adapters.onebot.v11 import Message

    user_extra = event.get_message().extract_plain_text().strip()

    user_content = f"我的抽牌结果：\n{cards_text}"
    if user_extra:
        user_content += f"\n\n我想问的方向：{user_extra}"

    messages = [
        {"role": "system", "content": _DIVINATION_PROMPT},
        {"role": "user", "content": user_content},
    ]

    reply = await chat_completion(
        messages,
        model="claude-haiku-4-5-20251001",
        temperature=0.9,
        max_tokens=300,
    )

    if not reply:
        await divination_cmd.finish(
            MessageSegment.at(user_id) + " 水晶球今天不太给力……稍后再试试吧"
        )

    reply = reply.strip().strip('"').strip("'")
    await divination_cmd.finish(MessageSegment.at(user_id) + " " + reply)
