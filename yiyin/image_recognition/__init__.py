"""
NoneBot2 图片识别插件（隐藏功能，需 /启用 图片识别）
- 对启用了该功能的群聊，自动识别每条消息中的图片（不含 QQ 表情 face）
- 调用 LLM 判断：1) 二次元/真人美女等 → 从 sao.json 随机回复
- 2) 食物 → 自动收集到食物图鉴并提示（名称仅供参考，可用 /补充名字 调整）
"""

import asyncio
import json
import random
import tempfile
from collections import deque
from datetime import datetime, timezone
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
SAO_JSON = PROJECT_ROOT / "assets" / "documents" / "sao.json"
IMAGE_RECOG_MODEL = "gpt-4o"
LOG_DIR = PROJECT_ROOT / "data" / "image_recognition"
LOG_FILE = LOG_DIR / "recognition.jsonl"

# LLM 输出格式：BEAUTY | FOOD:简短名称 | OTHER
BEAUTY_PROMPT = """你是一个严格的图片分类器。只看图片内容，按以下规则回复，且只回复一行，不要有任何其他文字。

规则：
1. BEAUTY：若图片是群友常转发的「色图」类内容——二次元涩图、动漫美少女、真人美女/性感照、诱人的女性形象或身体部位、男性向审美内容等 → 回复：BEAUTY
2. FOOD：若图片主体是真实可食用的食物/菜品（正餐、小吃、甜品、饮料等），回复时必须给出真实存在的食物名称，如「蛋炒饭」「拿铁咖啡」「炸鸡」，最多10字。禁止瞎编或使用非食物词汇，名称必须是具体食物而非抽象概念 → 回复：FOOD:具体食物名
3. 其他情况（风景、宠物、表情包、文字截图等）→ 回复：OTHER

只输出 BEAUTY、FOOD:xxx 或 OTHER 之一，不要解释。"""

# 图片 URL 去重：同一张图只请求识别一次（LRU 缓存，防止重复转发刷 API）
# 小内存服务器建议降低此值（如 500），减少内存占用
_RECOG_CACHE: set[str] = set()
_RECOG_CACHE_ORDER: deque[str] = deque()
_RECOG_CACHE_MAX = 500
_recog_cache_lock = asyncio.Lock()


def _append_recognition_log(
    image_url: str,
    llm_ok: bool,
    llm_error: str | None,
    llm_reply: str | None,
) -> None:
    """追加一条图片识别日志到 data/image_recognition/recognition.jsonl"""
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


async def _image_recognition_enabled(event: GroupMessageEvent) -> bool:
    """本群已启用图片识别功能"""
    return is_feature_enabled("image_recognition", str(event.group_id))


# GIF 文件头魔数
_GIF_MAGIC = (b"GIF87a", b"GIF89a")


async def _is_gif_by_download(url: str) -> bool:
    """下载图片到临时目录，检查后缀名或文件魔数是否为 GIF"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False
        with tempfile.TemporaryDirectory(prefix="image_recog_") as tmpdir:
            # 根据 Content-Disposition 或 URL 确定后缀名
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
            tmp_path.write_bytes(resp.content)
            if suffix == ".gif":
                return True
            # 后缀不明时从临时文件读取前 6 字节检查 GIF 魔数
            header = tmp_path.read_bytes()[:6]
            if len(header) >= 6 and header in _GIF_MAGIC:
                return True
            ct = (resp.headers.get("content-type") or "").lower()
            if "gif" in ct:
                return True
        return False
    except Exception:
        logger.debug("图片下载检测 GIF 失败: %s", url[:80])
        return False  # 失败时按非 GIF 处理，避免漏识别


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
    Rule(_has_image_no_face, _image_recognition_enabled),
    priority=60,
    block=False,
)


@image_recognition_matcher.handle()
async def handle_image_recognition(bot: Bot, event: GroupMessageEvent):
    """对群内图片消息进行识别并响应（Rule 已校验本群已启用图片识别）"""
    group_id = str(event.group_id)
    urls = _extract_image_urls(event)
    if not urls:
        return

    # 每条消息只处理第一张图片（避免刷屏）
    image_url = urls[0]

    if await _is_gif_by_download(image_url):
        return  # GIF 动图不参与识别

    if not await _claim_image_for_recognition(image_url):
        return  # 该图已识别过，跳过

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": BEAUTY_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    reply: str | None = None
    llm_error: str | None = None
    try:
        reply = await chat_completion(
            messages,
            model=IMAGE_RECOG_MODEL,
            temperature=0.1,
            max_tokens=64,
            timeout=25,
        )
    except Exception as e:
        llm_error = f"{type(e).__name__}: {e}"
        logger.exception("图片识别 LLM 调用失败")
        _append_recognition_log(image_url, llm_ok=False, llm_error=llm_error, llm_reply=None)
        return

    if not reply:
        _append_recognition_log(
            image_url,
            llm_ok=False,
            llm_error="API 未返回有效内容（可能是 YUNWU_API_KEY 未配置或请求失败）",
            llm_reply=None,
        )
        return

    _append_recognition_log(
        image_url,
        llm_ok=True,
        llm_error=None,
        llm_reply=reply,
    )

    rec_type, name = _parse_llm_response(reply)

    reply_seg = MessageSegment.reply(event.message_id)

    if rec_type == "BEAUTY":
        lines = _load_sao_lines()
        if lines:
            msg = random.choice(lines)
            await bot.send(event, reply_seg + MessageSegment.text(msg))

    elif rec_type == "FOOD":
        result = await add_food_from_image_url(group_id, image_url, name)
        if result:
            hint = "名称仅供参考，可使用 /补充名字 <id> <名字> 调整"
            await bot.send(event, reply_seg + MessageSegment.text(f"{result}\n{hint}"))
