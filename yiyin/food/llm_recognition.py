"""
食物图片识别相关的 LLM 公共能力。
- 自动食物收集：判断是否为食物，并给出一个简短名字
- 手动 /收集食物：未传名字时，尝试看图自动命名
"""

import asyncio
import base64

from nonebot.log import logger

from yiyin.llmapi import ChatCompletionTransportError, chat_completion

IMAGE_RECOG_MODEL = "gpt-4o"

# LLM 输出格式：FOOD:简短名称 | OTHER
FOOD_PROMPT = """你是一个严格的图片分类器，你需要判断图本身是不是一张真实食物的实拍照片。只看图片内容，按以下规则回复，且只回复一行，不要有任何其他文字。

规则：
1. 只有在以下条件同时满足时，才回复 FOOD:具体食物名
   - 图片主体是现实中可食用或可饮用的东西，都算食物；包括但不限于水果、坚果、蔬菜、肉类、海鲜、主食、零食、甜点、饮料、熟食、半成品、食材、调味食材、可直接入口或用于烹饪的原材料等。
   - 画面看起来像真实拍摄的食物照片，而不是截图、海报、拼图、商品图或界面截图。
   - 对于零食类食品，若图片为真实拍摄的成品，或其日常零售包装的实拍图（包括仅拍包装），也判定为食物；电商商品图、海报、宣传图除外。
   - 如果图片主体是单个食材或原材料，也应判定为 FOOD，例如『苹果』『核桃』『生菜』『牛肉』『鸡胸肉』『土豆』『番茄』『三文鱼』。
   - 名称必须是具体、真实存在的食物名称，如『蛋炒饭』『拿铁』『炸鸡』『牛排』『苹果』『核桃』『生菜』『牛肉』，最多10字。
2. 只要出现以下任一情况，都必须回复 OTHER
   - 菜单截图、点餐页面、外卖软件截图、商品列表、价格表。
   - 社交媒体截图、聊天截图、网页截图、带明显界面元素的帖子或短视频封面。
   - 图片里虽然出现食物，但主体是文字、排版、UI、评论区、账号页、广告、海报、包装、教程、食谱卡片。
   - 一张图里有很多小图、拼贴、九宫格、转发截图，食物不是唯一清晰主体。
   - 卡通食物、插画、表情包、玩具食物，而不是真实食物成品照片。

只输出 FOOD:xxx 或 OTHER 之一，不要解释。"""


def _build_data_url(image_bytes: bytes, content_type: str | None) -> str:
    """将图片二进制转为 data URL，避免模型侧再次拉取外链图片。"""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def parse_food_llm_response(text: str | None) -> tuple[str, str | None]:
    """解析 LLM 输出，返回 (type, name|null)。type 为 FOOD/OTHER"""
    if not text:
        return "OTHER", None
    raw = text.strip()
    upper = raw.upper()
    if upper.startswith("FOOD"):
        rest = raw[4:].lstrip(":： \t")
        return "FOOD", rest if rest else None
    return "OTHER", None


async def recognize_food_from_image_bytes(
    image_bytes: bytes,
    content_type: str | None,
    *,
    log_prefix: str = "食物识别",
) -> tuple[str, str | None]:
    """调用 LLM 判断图片是否为食物，并返回识别结果与建议名称。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": FOOD_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": _build_data_url(image_bytes, content_type)},
                },
            ],
        }
    ]
    reply: str | None = None
    for attempt in range(2):
        try:
            reply = await chat_completion(
                messages,
                model=IMAGE_RECOG_MODEL,
                temperature=0.1,
                max_tokens=64,
                timeout=90,
                raise_on_error=True,
            )
            if reply:
                break
            logger.warning(
                "{} LLM 成功返回但内容为空{}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
            )
            if attempt == 1:
                return "OTHER", None
        except ChatCompletionTransportError as e:
            logger.warning(
                "{} LLM 请求失败{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return "OTHER", None
        except Exception as e:
            logger.warning(
                "{} LLM 处理异常{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return "OTHER", None
        await asyncio.sleep(1)
    return parse_food_llm_response(reply)
