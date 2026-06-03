"""
自动食物收集（food 子模块，隐藏功能，需 /启用 自动食物收集）
- 对启用了该功能的群聊，自动识别每条消息中的食物图片
- 调用 LLM 判断图片是否为食物 → 自动收集到食物图鉴并提示（名称仅供参考，可用 /补充名字 调整）
- 常关，需群内 /启用 自动食物收集 后生效
"""

import asyncio
from collections import deque

import httpx
from nonebot import on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.food import add_food_from_image_url, get_group_labels
from yiyin.food.llm_recognition import recognize_food_with_labels_from_image_bytes
from yiyin.toggle import is_feature_enabled_async

# ==================== 配置 ====================
# 图片 URL 去重：同一张图只请求识别一次（LRU 缓存，防止重复转发刷 API）
_RECOG_CACHE: set[str] = set()
_RECOG_CACHE_ORDER: deque[str] = deque()
_RECOG_CACHE_MAX = 500
_recog_cache_lock = asyncio.Lock()

async def _claim_image_for_recognition(url: str) -> bool:
    """若该 URL 未被识别过则标记并返回 True，否则返回 False（跳过）"""
    async with _recog_cache_lock:
        if url in _RECOG_CACHE:
            return False
        _RECOG_CACHE.add(url)
        _RECOG_CACHE_ORDER.append(url)
        while len(_RECOG_CACHE_ORDER) > _RECOG_CACHE_MAX:
            old = _RECOG_CACHE_ORDER.popleft()
            _RECOG_CACHE.discard(old)
        return True


def _has_image_no_face(event: GroupMessageEvent) -> bool:
    """消息中有图片（type=image）。QQ 表情为 type=face，不参与识别。"""
    if not event.message:
        return False
    return any(seg.type == "image" for seg in event.message)


def _not_from_bot(event: GroupMessageEvent) -> bool:
    """排除 bot 自己发的消息"""
    return event.self_id != event.user_id


async def _auto_collect_enabled(bot: Bot, event: GroupMessageEvent) -> bool:
    """本群已启用自动食物收集功能"""
    return await is_feature_enabled_async(
        bot, "yiyin.food.auto_collect", str(event.group_id)
    )


# GIF 文件头魔数
_GIF_MAGIC = (b"GIF87a", b"GIF89a")


async def _fetch_image_info(
    url: str,
) -> tuple[bool, bytes | None, str | None]:
    """
    下载图片并检测是否 GIF。
    返回 (is_gif, content, content_type)。
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False, None, None
        content = resp.content
        content_type = resp.headers.get("content-type")
        disp = resp.headers.get("content-disposition", "").lower()
        is_gif = (
            ".gif" in url.lower()
            or "filename=" in disp and ".gif" in disp
            or len(content) >= 6 and content[:6] in _GIF_MAGIC
            or "gif" in (content_type or "").lower()
        )
        return is_gif, content, content_type
    except Exception:
        logger.debug("图片下载检测失败: {}", url[:80])
        return False, None, None


def _first_image_segment(event: GroupMessageEvent) -> MessageSegment | None:
    """获取消息中的第一张图片消息段。"""
    for seg in event.message:
        if seg.type == "image" and seg.data.get("url"):
            return seg
    return None


async def _should_handle_auto_collect(bot: Bot, event: GroupMessageEvent) -> bool:
    """仅在图片通过静默过滤后才进入 matcher。"""
    if not _not_from_bot(event):
        return False
    if not _has_image_no_face(event):
        return False
    if not await _auto_collect_enabled(bot, event):
        return False

    image_seg = _first_image_segment(event)
    if image_seg is None:
        return False

    summary = str(image_seg.data.get("summary") or "")
    if "动画表情" in summary:
        return False

    image_url = image_seg.data.get("url")
    if not image_url:
        return False

    is_gif, image_bytes, content_type = await _fetch_image_info(image_url)
    if is_gif:
        return False
    if not image_bytes:
        return False

    if not await _claim_image_for_recognition(image_url):
        return False

    setattr(event, "_yiyin_auto_collect_image_url", image_url)
    setattr(event, "_yiyin_auto_collect_image_bytes", image_bytes)
    setattr(event, "_yiyin_auto_collect_content_type", content_type)
    return True


# ==================== 注册 ====================
auto_collect_matcher = on_message(
    Rule(_should_handle_auto_collect),
    priority=60,
    block=False,
)


@auto_collect_matcher.handle()
async def handle_auto_collect(bot: Bot, event: GroupMessageEvent):
    """对群内图片消息进行识别并响应（Rule 已完成静默过滤与图片预取）"""
    group_id = str(event.group_id)
    image_url = getattr(event, "_yiyin_auto_collect_image_url", None)
    image_bytes = getattr(event, "_yiyin_auto_collect_image_bytes", None)
    content_type = getattr(event, "_yiyin_auto_collect_content_type", None)
    if not image_url or not image_bytes:
        return

    result = await recognize_food_with_labels_from_image_bytes(
        image_bytes,
        content_type,
        label_pool=get_group_labels(group_id),
        log_prefix="自动食物收集",
    )

    if result.get("type") == "FOOD":
        save_result = await add_food_from_image_url(
            group_id,
            image_url,
            result.get("name") if isinstance(result.get("name"), str) else None,
            tags=result.get("tags") if isinstance(result.get("tags"), list) else [],
        )
        if save_result:
            reply_seg = MessageSegment.reply(event.message_id)
            hint = "名称仅供参考，可使用 /补充名字 <id> <名字> 调整"
            await bot.send(event, reply_seg + MessageSegment.text(f"{save_result}\n{hint}"))
