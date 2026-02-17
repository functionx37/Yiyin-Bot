"""
NoneBot2 塔罗牌插件
- 命令：/抽塔罗牌  — 随机抽取大阿卡纳塔罗牌（0-21），随机正位/逆位，发送图文消息
- 命令：/抽十连    — 一次性抽取 10 张塔罗牌，精简输出，每用户每天限用一次
"""

import json
import random
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

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
    await tarot_cmd.finish(msg)


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
    for _ in range(10):
        card_id = random.randint(0, 21)
        is_upright = random.choice([True, False])
        card = TAROT_MAP[card_id]
        orientation = "正位" if is_upright else "逆位"
        lines.append(f"【{card['name_zh']}】（{card['name_en']}）{orientation}")

    result = "\n".join(lines)
    msg = MessageSegment.at(user_id) + " 十连抽结果：\n" + result

    await tarot_ten_cmd.finish(msg)
