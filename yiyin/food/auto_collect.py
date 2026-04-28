"""
自动食物收集（food 子模块，隐藏功能，需 /启用 自动食物收集）
- 对启用了该功能的群聊，自动识别每条消息中的食物图片
- 调用 LLM 判断图片是否为食物 → 自动收集到食物图鉴并提示（名称仅供参考，可用 /补充名字 调整）
- 常关，需群内 /启用 自动食物收集 后生效
"""

import asyncio
import tempfile
from collections import deque
from pathlib import Path

import httpx
from nonebot import on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.food import add_food_from_image_url
from yiyin.food.llm_recognition import recognize_food_from_image_bytes
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

# 表情包过滤：长宽阈值（像素），且比例接近 1:1 时跳过识别
_STICKER_MAX_DIM_RELAXED = 384  # 最长边不超过此值
_STICKER_ASPECT_RATIO_MIN = 0.85  # min/max 至少 0.85，即接近正方形
# 文件过小且接近正方形 → 疑似表情包（字节）
_STICKER_MAX_SIZE_SMALL = 32 * 1024  # 32KB
# URL 中疑似表情包的关键词（小写匹配）
_STICKER_URL_KEYWORDS = ("sticker", "emoji", "gface", "face", "meme", "斗图", "贴图")


def _is_sticker_like(width: int, height: int) -> bool:
    """判断是否为小尺寸或接近正方形的图片（疑似表情包）。满足其一即排除。"""
    if width <= 0 or height <= 0:
        return False
    max_dim = max(width, height)
    min_dim = min(width, height)
    aspect_ratio = min_dim / max_dim
    return max_dim <= _STICKER_MAX_DIM_RELAXED or aspect_ratio >= _STICKER_ASPECT_RATIO_MIN


def _is_sticker_url(url: str) -> bool:
    """URL 路径中含表情包相关关键词"""
    lower = url.lower()
    return any(kw in lower for kw in _STICKER_URL_KEYWORDS)


def _is_small_file_sticker(size_bytes: int, width: int | None, height: int | None) -> bool:
    """文件很小且尺寸接近正方形 → 疑似表情包"""
    if size_bytes > _STICKER_MAX_SIZE_SMALL or width is None or height is None:
        return False
    max_dim = max(width, height)
    min_dim = min(width, height)
    return max_dim <= _STICKER_MAX_DIM_RELAXED and min_dim / max_dim >= _STICKER_ASPECT_RATIO_MIN


async def _fetch_image_info(
    url: str,
) -> tuple[bool, int | None, int | None, int, bytes | None, str | None]:
    """
    下载图片并检测：是否 GIF、宽度、高度、文件大小。
    返回 (is_gif, width, height, size_bytes, content, content_type)，
    解析失败时 width/height 为 None。
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False, None, None, 0, None, None
        content = resp.content
        content_type = resp.headers.get("content-type")
        size_bytes = len(content)
        with tempfile.TemporaryDirectory(prefix="image_recog_") as tmpdir:
            suffix = ".bin"
            disp = resp.headers.get("content-disposition", "")
            if "filename=" in disp.lower():
                for part in disp.split(";"):
                    if "filename=" in part.lower():
                        name = part.split("=", 1)[1].strip(" \"'")
                        if "." in name:
                            suffix = "." + name.rsplit(".", 1)[1].lower()
                        break
            elif ".gif" in url.lower():
                suffix = ".gif"
            tmp_path = Path(tmpdir) / f"img{suffix}"
            tmp_path.write_bytes(content)
            # 检测 GIF
            is_gif = suffix == ".gif" or (
                len(content) >= 6 and content[:6] in _GIF_MAGIC
            ) or "gif" in (resp.headers.get("content-type") or "").lower()
            # 解析尺寸（PIL）
            width, height = None, None
            try:
                from PIL import Image

                with Image.open(tmp_path) as img:
                    width, height = img.size
            except Exception:
                pass
        return is_gif, width, height, size_bytes, content, content_type
    except Exception:
        logger.debug("图片下载检测失败: {}", url[:80])
        return False, None, None, 0, None, None


def _extract_image_urls(event: GroupMessageEvent) -> list[str]:
    """提取消息中所有图片的 URL（仅 type=image）"""
    urls: list[str] = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                urls.append(url)
    return urls


def _is_subtype_sticker(seg_data: dict) -> bool:
    """OneBot subType: 1=表情包 2=热图 3=斗图 4=智图 5=贴图，全部跳过"""
    st = seg_data.get("subType")
    if st is None:
        return False
    return st in (1, "1", 2, "2", 3, "3", 4, "4", 5, "5")


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
    image_url = image_seg.data.get("url")
    if not image_url:
        return False

    # [1] URL 中含表情包关键词 → 跳过
    if _is_sticker_url(image_url):
        return False

    # [2] OneBot subType 标记为表情包/热图/斗图/智图/贴图 → 跳过
    if _is_subtype_sticker(image_seg.data):
        return False

    # [3] 下载并检测
    is_gif, width, height, size_bytes, image_bytes, content_type = await _fetch_image_info(
        image_url
    )
    if is_gif:
        return False
    if width is not None and height is not None and _is_sticker_like(width, height):
        return False
    if _is_small_file_sticker(size_bytes, width, height):
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

    rec_type, name = await recognize_food_from_image_bytes(
        image_bytes,
        content_type,
        log_prefix="自动食物收集",
    )

    if rec_type == "FOOD":
        result = await add_food_from_image_url(group_id, image_url, name)
        if result:
            reply_seg = MessageSegment.reply(event.message_id)
            hint = "名称仅供参考，可使用 /补充名字 <id> <名字> 调整"
            await bot.send(event, reply_seg + MessageSegment.text(f"{result}\n{hint}"))
