"""
NoneBot2 塔罗牌插件
- 命令：/抽塔罗牌
- 功能：随机抽取大阿卡纳塔罗牌（0-21），随机正位/逆位，发送图文消息
"""

import json
import random
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

# ==================== 注册命令 ====================
tarot_cmd = on_command("抽塔罗牌", aliases=set(), priority=10, block=True)


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
