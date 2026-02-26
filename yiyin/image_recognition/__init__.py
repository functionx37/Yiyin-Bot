"""
NoneBot2 图片识别插件（隐藏功能，需 /启用 图片识别）
- 对启用了该功能的群聊，自动识别每条消息中的图片（不含 QQ 表情 face）
- 调用 LLM 判断：1) 二次元/真人美女等 → 从 sao.json 随机回复
- 2) 食物 → 自动收集到食物图鉴并提示（名称仅供参考，可用 /补充名字 调整）
"""

import json
import random
from pathlib import Path

from nonebot import on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.llmapi import chat_completion
from yiyin.food import add_food_from_image_url

# ==================== 配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SAO_JSON = PROJECT_ROOT / "assets" / "documents" / "sao.json"
IMAGE_RECOG_MODEL = "gemini-3-flash-preview"  # Vision 模型；若不可用可改为 gpt-4o-mini

# LLM 输出格式：BEAUTY | FOOD:简短名称 | OTHER
BEAUTY_PROMPT = """你是一个严格的图片分类器。只看图片内容，按以下规则回复，且只回复一行，不要有任何其他文字。

规则：
1. 若图片包含：二次元/动漫风格的美少女、真人美女、性感/美丽的女性身体部位、诱人的可爱的女性形象、男性审美喜爱的女性相关内容 → 回复：BEAUTY
2. 若图片主要是食物/菜品（包括正餐、小吃、甜品、饮料等） → 回复：FOOD:简短名称（如「蛋炒饭」「拿铁咖啡」，最多10字）
3. 其他情况 → 回复：OTHER

只输出 BEAUTY、FOOD:xxx 或 OTHER 之一，不要解释。"""


def _has_image_no_face(event: GroupMessageEvent) -> bool:
    """消息中有图片（type=image）。QQ 表情为 type=face，不参与识别。"""
    if not event.message:
        return False
    return any(seg.type == "image" for seg in event.message)


def _extract_image_urls(event: GroupMessageEvent) -> list[str]:
    """提取消息中所有图片的 URL（仅 type=image）"""
    urls: list[str] = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                urls.append(url)
    return urls


def _load_sao_lines() -> list[str]:
    """加载 sao.json 中的字符串列表"""
    if not SAO_JSON.exists():
        return []
    try:
        with open(SAO_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        return []
    except Exception:
        logger.exception("加载 sao.json 失败")
        return []


def _parse_llm_response(text: str) -> tuple[str, str | None]:
    """解析 LLM 输出，返回 (type, name|null)。type 为 BEAUTY/FOOD/OTHER"""
    if not text:
        return "OTHER", None
    raw = text.strip()
    upper = raw.upper()
    if upper == "BEAUTY" or upper.startswith("BEAUTY") and "FOOD" not in upper:
        return "BEAUTY", None
    if upper.startswith("FOOD"):
        rest = raw[4:].lstrip(":： \t")
        return "FOOD", rest if rest else None
    return "OTHER", None


# ==================== 注册 ====================
image_recognition_matcher = on_message(
    Rule(_has_image_no_face),
    priority=60,
    block=False,
)


@image_recognition_matcher.handle()
async def handle_image_recognition(bot: Bot, event: GroupMessageEvent):
    """对群内图片消息进行识别并响应（仅当群已启用图片识别时，由 toggle 预处理器放行）"""
    group_id = str(event.group_id)
    urls = _extract_image_urls(event)
    if not urls:
        return

    # 每条消息只处理第一张图片（避免刷屏）
    image_url = urls[0]

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": BEAUTY_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    try:
        reply = await chat_completion(
            messages,
            model=IMAGE_RECOG_MODEL,
            temperature=0.1,
            max_tokens=64,
            timeout=25,
        )
    except Exception:
        logger.exception("图片识别 LLM 调用失败")
        return

    if not reply:
        return

    rec_type, name = _parse_llm_response(reply)

    if rec_type == "BEAUTY":
        lines = _load_sao_lines()
        if lines:
            msg = random.choice(lines)
            await bot.send(event, msg)

    elif rec_type == "FOOD":
        result = await add_food_from_image_url(group_id, image_url, name)
        if result:
            hint = "名称仅供参考，可使用 /补充名字 <id> <名字> 调整"
            await bot.send(event, f"{result}\n{hint}")
