"""QQ 图片素材上传与持久缓存；不需要公网图片域名。"""

import asyncio
import hashlib
import json
from pathlib import Path
from time import time

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.exception import NetworkError
from nonebot.drivers import Request
from nonebot.log import logger

CACHE_DIR = Path(__file__).resolve().parents[2] / "data/tarot"
_caches: dict[str, dict] = {}
_locks: dict[str, asyncio.Lock] = {}


def _cache(bot_id: str) -> dict:
    if bot_id not in _caches:
        try:
            value = json.loads((CACHE_DIR / f"{bot_id}.json").read_text(encoding="utf-8"))
            _caches[bot_id] = value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            _caches[bot_id] = {}
    return _caches[bot_id]


def _save(bot_id: str):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{bot_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(_cache(bot_id), ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.warning("无法保存 QQ 图片素材缓存，请检查 data/tarot 目录权限")


async def image_url(bot: Bot, image: bytes, *, refresh: bool = False) -> str:
    digest = hashlib.sha256(image).hexdigest()
    async with _locks.setdefault(bot.self_id, asyncio.Lock()):
        cache = _cache(bot.self_id)
        entry = cache.get(digest)
        if not refresh and isinstance(entry, dict) and entry.get("expires_at", 0) > time():
            url = entry.get("url", "")
            if isinstance(url, str) and url.startswith("qqbot-media://image/v1/"):
                return url
        if len(image) > 10_000_000:
            raise ValueError("QQ 图片素材超过 10 MB")
        md5 = hashlib.md5(image).hexdigest()
        base = bot.adapter.get_api_base().joinpath("v2", "media-images")
        prepared = await bot._request(Request("POST", base.joinpath("prepare"), json={
            "file_name": f"tarot-{digest[:16]}.png", "file_size": len(image),
            "content_type": "image/png", "md5": md5, "sha1": hashlib.sha1(image).hexdigest(),
        }))
        # The pre-signed COS request must not carry QQ's Authorization header.
        try:
            response = await bot.adapter.request(Request(
                "PUT", prepared["presigned_url"], content=image,
                headers={"Content-Type": "image/png"}, timeout=30,
            ))
        except Exception as error:
            raise NetworkError("QQ image upload failed") from error
        if not 200 <= response.status_code < 300:
            raise NetworkError("QQ image upload rejected")
        etag = response.headers.get("ETag", "").strip('"')
        if etag.lower() != md5:
            raise ValueError("QQ 图片上传校验失败")
        completed = await bot._request(Request("POST", base.joinpath("complete"), json={
            "upload_id": prepared["upload_id"], "md5": etag,
        }))
        url = completed.get("url", "")
        if not isinstance(url, str) or not url.startswith("qqbot-media://image/v1/"):
            raise ValueError("QQ 未返回有效图片素材地址")
        ttl = int(completed.get("ttl", 7_776_000))
        cache[digest] = {"image_id": completed["image_id"], "url": url,
                         "ttl": ttl, "expires_at": time() + ttl}
        _save(bot.self_id)
        return url


async def mark_used(bot: Bot, image: bytes):
    async with _locks.setdefault(bot.self_id, asyncio.Lock()):
        entry = _cache(bot.self_id).get(hashlib.sha256(image).hexdigest())
        if entry:
            entry["expires_at"] = time() + entry["ttl"]
            _save(bot.self_id)
