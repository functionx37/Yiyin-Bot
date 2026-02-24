"""
对称图片子功能
- 命令：/对称 [左/右/上/下] [图片]
- 将图片按指定方向对称翻转，支持动图和透明通道
"""

import asyncio
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageSequence, UnidentifiedImageError
from nonebot import on_command
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源限制 ====================
MAX_IMAGE_PIXELS = 4_000_000
MAX_GIF_FRAMES = 100
MAX_CONCURRENT = 1
QUEUE_TIMEOUT = 10
PROCESS_TIMEOUT = 30

_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meme")

symmetric_cmd = on_command("对称", priority=10, block=True)

VALID_DIRECTIONS = {"左", "右", "上", "下"}
DEFAULT_DIRECTION = "左"


def _downscale_if_needed(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w * h > MAX_IMAGE_PIXELS:
        scale = (MAX_IMAGE_PIXELS / (w * h)) ** 0.5
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return img


def _apply_symmetric(img: Image.Image, direction: str) -> Image.Image:
    w, h = img.size

    if direction == "左":
        half = w // 2
        left = img.crop((0, 0, half, h))
        mirrored = left.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        result = Image.new(img.mode, (half * 2, h))
        result.paste(left, (0, 0))
        result.paste(mirrored, (half, 0))

    elif direction == "右":
        half = w // 2
        right = img.crop((w - half, 0, w, h))
        mirrored = right.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        result = Image.new(img.mode, (half * 2, h))
        result.paste(mirrored, (0, 0))
        result.paste(right, (half, 0))

    elif direction == "上":
        half = h // 2
        top = img.crop((0, 0, w, half))
        mirrored = top.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        result = Image.new(img.mode, (w, half * 2))
        result.paste(top, (0, 0))
        result.paste(mirrored, (0, half))

    elif direction == "下":
        half = h // 2
        bottom = img.crop((0, h - half, w, h))
        mirrored = bottom.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        result = Image.new(img.mode, (w, half * 2))
        result.paste(mirrored, (0, 0))
        result.paste(bottom, (0, half))

    else:
        result = img.copy()

    return result


def _process_static(img: Image.Image, direction: str) -> bytes:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img = _downscale_if_needed(img)
    result = _apply_symmetric(img, direction)
    buf = BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _process_animated(img: Image.Image, direction: str) -> bytes:
    n_frames = getattr(img, "n_frames", 1)
    if n_frames > MAX_GIF_FRAMES:
        raise ValueError(
            f"动图帧数过多（{n_frames} 帧，上限 {MAX_GIF_FRAMES} 帧），请使用更短的动图"
        )
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(img):
        duration = frame.info.get("duration", 100)
        if duration <= 0:
            duration = 100
        durations.append(duration)
        rgba_frame = frame.convert("RGBA")
        rgba_frame = _downscale_if_needed(rgba_frame)
        processed = _apply_symmetric(rgba_frame, direction)
        frames.append(processed)
    if not frames:
        raise ValueError("动图中没有有效帧")
    gif_frames: list[Image.Image] = []
    for f in frames:
        alpha = f.split()[3]
        p_frame = f.convert("RGB").convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=255
        )
        mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
        p_frame.paste(255, mask)
        p_frame.info["transparency"] = 255
        gif_frames.append(p_frame)
    buf = BytesIO()
    gif_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=gif_frames[1:],
        loop=img.info.get("loop", 0),
        duration=durations,
        disposal=2,
    )
    buf.seek(0)
    return buf.getvalue()


def _do_process(image_bytes: bytes, direction: str) -> bytes:
    try:
        img = Image.open(BytesIO(image_bytes))
    except UnidentifiedImageError:
        raise ValueError("无法识别的图片格式，请发送 PNG、JPG 或 GIF 图片")
    with img:
        is_animated = getattr(img, "is_animated", False)
        if is_animated:
            return _process_animated(img, direction)
        return _process_static(img, direction)


def _extract_image_url(msg: Message) -> str | None:
    for seg in msg:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                return url
    return None


async def _download_image(url: str, save_path: Path) -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)


@symmetric_cmd.handle()
async def handle_symmetric(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    text = args.extract_plain_text().strip()
    direction = DEFAULT_DIRECTION
    if text and text[0] in VALID_DIRECTIONS:
        direction = text[0]

    image_url = _extract_image_url(args)
    if not image_url and event.reply:
        image_url = _extract_image_url(event.reply.message)

    if not image_url:
        await symmetric_cmd.finish(
            "请附带图片或回复一张图片，例如：\n"
            "/对称 左 [图片]\n/对称 [图片]\n回复图片消息并发送 /对称 右"
        )

    temp_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="meme_") as tmpdir:
        temp_path = Path(tmpdir) / temp_id
        try:
            await _download_image(image_url, temp_path)
        except Exception:
            logger.exception(f"下载对称图片失败: {image_url}")
            await symmetric_cmd.finish("图片下载失败，请稍后重试")

        try:
            await asyncio.wait_for(_semaphore.acquire(), timeout=QUEUE_TIMEOUT)
        except asyncio.TimeoutError:
            await symmetric_cmd.finish("当前有其他图片正在处理中，请稍后再试")

        try:
            image_bytes = temp_path.read_bytes()
            loop = asyncio.get_running_loop()
            result_bytes = await asyncio.wait_for(
                loop.run_in_executor(_executor, _do_process, image_bytes, direction),
                timeout=PROCESS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await symmetric_cmd.finish("图片处理超时，请使用较小的图片或更短的动图")
        except ValueError as e:
            await symmetric_cmd.finish(str(e))
        except Exception as e:
            await symmetric_cmd.finish(f"图片处理失败：{e}")
        finally:
            _semaphore.release()

    await symmetric_cmd.finish(MessageSegment.image(result_bytes))
