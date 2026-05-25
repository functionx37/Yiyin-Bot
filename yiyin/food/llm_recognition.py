"""
食物图片识别相关的 LLM 公共能力。
- 自动食物收集：判断是否为食物，并给出简短名字与可选标签
- 手动 /收集食物：未传名字时，尝试看图自动命名
"""

import asyncio
import base64
import json

from nonebot.log import logger

from yiyin.llmapi import ChatCompletionTransportError, chat_completion

IMAGE_RECOG_MODEL = "gpt-4o"

FOOD_PROMPT = """你是一个严格的图片分类器，你需要判断图本身是不是一张真实食物的实拍照片。只看图片内容，按以下规则回复，且只回复一行，不要有任何其他文字。

规则：
1. 只有在以下条件同时满足时，才回复 FOOD:具体食物名
   - 图片主体是现实中可食用或可饮用的东西，都算食物；包括但不限于水果、坚果、蔬菜、肉类、海鲜、主食、零食、甜点、饮料、熟食、半成品、食材、调味食材、可直接入口或用于烹饪的原材料等。
   - 画面看起来像真实拍摄的食物照片，而不是截图、海报、拼图、商品图或界面截图。
   - 对于零食类食品，若图片为真实拍摄的成品，或其日常零售包装的实拍图（包括仅拍包装），也判定为食物；电商商品图、海报、宣传图除外。
   - 如果图片主体是单个食材或原材料，也应判定为 FOOD，例如『苹果』『核桃』『生菜』『牛肉』『鸡胸肉』『土豆』『番茄』『三文鱼』。
   - 名称必须是具体、真实存在的食物名称，最多10字。
2. 只要出现以下任一情况，都必须回复 OTHER
   - 菜单截图、点餐页面、外卖软件截图、商品列表、价格表。
   - 社交媒体截图、聊天截图、网页截图、带明显界面元素的帖子或短视频封面。
   - 图片里虽然出现食物，但主体是文字、排版、UI、评论区、账号页、广告、海报、包装、教程、食谱卡片。
   - 一张图里有很多小图、拼贴、九宫格、转发截图，食物不是唯一清晰主体。
   - 卡通食物、插画、表情包、玩具食物，而不是真实食物成品照片。

只输出 FOOD:xxx 或 OTHER 之一，不要解释。"""

FOOD_NAME_PROMPT = """这是一张已经明确要收集到食物图鉴中的图片，请你仅根据图片内容，为它生成一个简短、具体、自然的食物名称。

要求：
1. 默认把图片主体视为食物，不要再判断它是不是食物。
2. 只回复一个名称，不要解释，不要加前缀，不要加标点。
3. 名称尽量具体，如“蛋炒饭”“拿铁”“炸鸡”“苹果”“薯片”“火锅”。
4. 最多 10 个字。
5. 如果图片信息不足，就给一个尽量宽泛但仍像食物名的名称，如“点心”“饮料”“零食”。
"""

FOOD_WITH_LABELS_PROMPT = """你在帮一个群聊收集食物图鉴。你需要判断图片是不是应该收集的真实食物图片；如果是，再给出一个简短食物名，并尽量从给定标签池里挑选最合适的标签。

你必须只输出一行 JSON，不要解释，不要加 markdown。

输出格式：
{{"type":"FOOD","name":"食物名","tags":["标签1","标签2"]}}
或
{{"type":"OTHER"}}

要求：
1. 是否属于 FOOD 的判断标准，与普通食物识别一致：必须是现实中的真实食物实拍，而不是菜单截图、海报、拼图、UI 截图、卡通食物等。
2. 如果 type=FOOD：
   - name 必须是具体、自然的食物名称，最多 10 个字。
   - tags 必须优先从我给你的标签池中选择，最多 4 个。
   - 如果没有合适标签，就返回空数组。
   - 不要编造不在标签池里的新标签。
3. 如果 type=OTHER，只返回 {{"type":"OTHER"}}。

标签池：
{label_pool}
"""


def _build_data_url(image_bytes: bytes, content_type: str | None) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def parse_food_llm_response(text: str | None) -> tuple[str, str | None]:
    if not text:
        return "OTHER", None
    raw = text.strip()
    upper = raw.upper()
    if upper.startswith("FOOD"):
        rest = raw[4:].lstrip(":： \t")
        return "FOOD", rest if rest else None
    return "OTHER", None


def _parse_food_with_labels_response(text: str | None) -> dict[str, object]:
    if not text:
        return {"type": "OTHER", "name": None, "tags": []}
    try:
        data = json.loads(text.strip())
    except Exception:
        logger.warning("食物标签识别返回非 JSON：{}", text)
        return {"type": "OTHER", "name": None, "tags": []}

    if not isinstance(data, dict):
        return {"type": "OTHER", "name": None, "tags": []}
    rec_type = str(data.get("type") or "").upper()
    if rec_type != "FOOD":
        return {"type": "OTHER", "name": None, "tags": []}
    name = data.get("name")
    tags = data.get("tags")
    if not isinstance(name, str):
        name = None
    if not isinstance(tags, list):
        tags = []
    clean_tags = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
    return {"type": "FOOD", "name": name.strip()[:10] if name and name.strip() else None, "tags": clean_tags[:4]}


async def recognize_food_from_image_bytes(
    image_bytes: bytes,
    content_type: str | None,
    *,
    log_prefix: str = "食物识别",
) -> tuple[str, str | None]:
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


async def recognize_food_with_labels_from_image_bytes(
    image_bytes: bytes,
    content_type: str | None,
    *,
    label_pool: list[str] | None = None,
    log_prefix: str = "食物识别",
) -> dict[str, object]:
    prompt = FOOD_WITH_LABELS_PROMPT.format(
        label_pool="、".join(label_pool or []) or "（当前没有可选标签）"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
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
                max_tokens=128,
                timeout=90,
                raise_on_error=True,
            )
            if reply:
                return _parse_food_with_labels_response(reply)
            logger.warning(
                "{} LLM 成功返回但内容为空{}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
            )
            if attempt == 1:
                return {"type": "OTHER", "name": None, "tags": []}
        except ChatCompletionTransportError as e:
            logger.warning(
                "{} LLM 请求失败{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return {"type": "OTHER", "name": None, "tags": []}
        except Exception as e:
            logger.warning(
                "{} LLM 处理异常{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return {"type": "OTHER", "name": None, "tags": []}
        await asyncio.sleep(1)
    return {"type": "OTHER", "name": None, "tags": []}


async def suggest_food_name_from_image_bytes(
    image_bytes: bytes,
    content_type: str | None,
    *,
    log_prefix: str = "食物命名",
) -> str | None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": FOOD_NAME_PROMPT},
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
                max_tokens=32,
                timeout=90,
                raise_on_error=True,
            )
            if reply and reply.strip():
                return reply.strip()[:10]
            logger.warning(
                "{} LLM 成功返回但内容为空{}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
            )
            if attempt == 1:
                return None
        except ChatCompletionTransportError as e:
            logger.warning(
                "{} LLM 请求失败{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return None
        except Exception as e:
            logger.warning(
                "{} LLM 处理异常{}: {}",
                log_prefix,
                f" (第{attempt + 1}次)" if attempt == 0 else "（重试后）",
                e,
            )
            if attempt == 1:
                logger.exception("{} LLM 重试后仍失败", log_prefix)
                return None
        await asyncio.sleep(1)
    return None
