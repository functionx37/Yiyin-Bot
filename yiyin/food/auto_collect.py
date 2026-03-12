"""
自动食物收集（food 子模块，隐藏功能，需 /启用 自动食物收集）
- 对启用了该功能的群聊，自动识别每条消息中的食物图片
- 调用 LLM 判断图片是否为食物 → 自动收集到食物图鉴并提示（名称仅供参考，可用 /补充名字 调整）
- 常关，需群内 /启用 自动食物收集 后生效
"""

import asyncio
import json
import tempfile
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from nonebot import on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.rule import Rule

from yiyin.food import add_food_from_image_url
from yiyin.llmapi import chat_completion
from yiyin.toggle import is_feature_enabled

# ==================== 配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGE_RECOG_MODEL = "gpt-4o"
LOG_DIR = PROJECT_ROOT / "data" / "food" / "auto_collect"
LOG_FILE = LOG_DIR / "recognition.jsonl"

# LLM 输出格式：FOOD:简短名称 | OTHER
FOOD_PROMPT = """你是一个严格的图片分类器。只看图片内容，按以下规则回复，且只回复一行，不要有任何其他文字。

规则：
1. FOOD：若图片主体是真实可食用的食物/菜品（正餐、小吃、甜品、饮料等），回复时必须给出真实存在的食物名称，如『蛋炒饭』『拿铁咖啡』『炸鸡』，最多10字。禁止瞎编或使用非食物词汇，名称必须是具体食物而非抽象概念 → 回复：FOOD:具体食物名
2. 其他情况（风景、宠物、表情包、文字截图、人物等）→ 回复：OTHER

只输出 FOOD:xxx 或 OTHER 之一，不要解释。"""

# 图片 URL 去重：同一张图只请求识别一次（LRU 缓存，防止重复转发刷 API）
_RECOG_CACHE: set[str] = set()
_RECOG_CACHE_ORDER: deque[str] = deque()
_RECOG_CACHE_MAX = 500
_recog_cache_lock = asyncio.Lock()

# 日志清理：上次执行清理的时间（秒），避免每次追加都全量扫描
_last_cleanup_time: float = 0
_CLEANUP_INTERVAL_SEC = 3600  # 至少间隔 1 小时再执行一次清理


def _cleanup_old_recognition_logs() -> None:
    """清除一天以前的自动食物收集日志"""
    if not LOG_FILE.exists():
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        kept_lines: list[str] = []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("time")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            kept_lines.append(line)
                except (json.JSONDecodeError, ValueError):
                    kept_lines.append(line)  # 解析失败则保留
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(kept_lines) + ("\n" if kept_lines else ""))
    except Exception:
        logger.exception("自动食物收集日志清理失败")


def _append_recognition_log(
    image_url: str,
    llm_ok: bool,
    llm_error: str | None,
    llm_reply: str | None,
) -> None:
    """追加一条自动食物收集日志"""
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time >= _CLEANUP_INTERVAL_SEC:
        _cleanup_old_recognition_logs()
        _last_cleanup_time = now

    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "image_url": image_url,
        "llm_success": llm_ok,
        "llm_error": llm_error,
        "llm_reply": llm_reply,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


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


async def _auto_collect_enabled(event: GroupMessageEvent) -> bool:
    """本群已启用自动食物收集功能"""
    return is_feature_enabled("yiyin.food.auto_collect", str(event.group_id))


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


async def _fetch_image_info(url: str) -> tuple[bool, int | None, int | None, int]:
    """
    下载图片并检测：是否 GIF、宽度、高度、文件大小。
    返回 (is_gif, width, height, size_bytes)，解析失败时 width/height 为 None。
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False, None, None, 0
        content = resp.content
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
        return is_gif, width, height, size_bytes
    except Exception:
        logger.debug("图片下载检测失败: %s", url[:80])
        return False, None, None, 0


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


def _parse_llm_response(text: str) -> tuple[str, str | None]:
    """解析 LLM 输出，返回 (type, name|null)。type 为 FOOD/OTHER"""
    if not text:
        return "OTHER", None
    raw = text.strip()
    upper = raw.upper()
    if upper.startswith("FOOD"):
        rest = raw[4:].lstrip(":： \t")
        return "FOOD", rest if rest else None
    return "OTHER", None


# 启动时执行一次日志清理
_cleanup_old_recognition_logs()
_last_cleanup_time = time.time()

# ==================== 注册 ====================
auto_collect_matcher = on_message(
    Rule(_not_from_bot, _has_image_no_face, _auto_collect_enabled),
    priority=60,
    block=False,
)


@auto_collect_matcher.handle()
async def handle_auto_collect(bot: Bot, event: GroupMessageEvent):
    """对群内图片消息进行识别并响应（Rule 已校验本群已启用自动食物收集）"""
    group_id = str(event.group_id)
    urls = _extract_image_urls(event)
    if not urls:
        return

    # 每条消息只处理第一张图片（避免刷屏）
    image_url = urls[0]

    # [1] URL 中含表情包关键词 → 跳过
    if _is_sticker_url(image_url):
        return

    # [2] OneBot subType 标记为表情包/热图/斗图/智图/贴图 → 跳过
    for seg in event.message:
        if seg.type == "image" and seg.data.get("url") == image_url:
            if _is_subtype_sticker(seg.data):
                return
            break

    # [3] 下载并检测
    is_gif, width, height, size_bytes = await _fetch_image_info(image_url)
    if is_gif:
        return  # [3a] GIF 动图一律排除（多为表情包）
    if width is not None and height is not None and _is_sticker_like(width, height):
        return  # [3b] 小尺寸接近正方形 → 疑似表情包
    if _is_small_file_sticker(size_bytes, width, height):
        return  # [3c] 文件很小 + 接近正方形 → 疑似表情包

    if not await _claim_image_for_recognition(image_url):
        return  # 该图已识别过，跳过

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": FOOD_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    reply: str | None = None
    llm_error: str | None = None
    for attempt in range(2):
        try:
            reply = await chat_completion(
                messages,
                model=IMAGE_RECOG_MODEL,
                temperature=0.1,
                max_tokens=64,
                timeout=25,
            )
            if reply:
                break
            logger.warning("自动食物收集 LLM 返回空内容%s", f" (第{attempt+1}次)" if attempt == 0 else "（重试后）")
            if attempt == 1:
                llm_error = "API 未返回有效内容（可能是 YUNWU_API_KEY 未配置或请求失败）"
                _append_recognition_log(image_url, llm_ok=False, llm_error=llm_error, llm_reply=None)
                return
        except Exception as e:
            llm_error = f"{type(e).__name__}: {e}"
            logger.warning("自动食物收集 LLM 调用失败%s: %s", f" (第{attempt+1}次)" if attempt == 0 else "（重试后）", e)
            if attempt == 1:
                logger.exception("自动食物收集 LLM 重试后仍失败")
                _append_recognition_log(image_url, llm_ok=False, llm_error=llm_error, llm_reply=None)
                return
        await asyncio.sleep(1)  # 重试前稍等

    _append_recognition_log(
        image_url,
        llm_ok=True,
        llm_error=None,
        llm_reply=reply,
    )

    rec_type, name = _parse_llm_response(reply)

    if rec_type == "FOOD":
        result = await add_food_from_image_url(group_id, image_url, name)
        if result:
            reply_seg = MessageSegment.reply(event.message_id)
            hint = "名称仅供参考，可使用 /补充名字 <id> <名字> 调整"
            await bot.send(event, reply_seg + MessageSegment.text(f"{result}\n{hint}"))
