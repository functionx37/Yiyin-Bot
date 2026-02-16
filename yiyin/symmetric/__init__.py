"""
NoneBot2 对称图片插件
- 命令：/对称 [左/右/上/下] [图片]
- 功能：将图片按指定方向对称翻转，支持动图和透明通道
- 支持回复图片消息进行对称处理
"""

import uuid
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageSequence, UnidentifiedImageError
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

# ==================== 路径配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMP_DIR = PROJECT_ROOT / "data" / "symmetric" / "temp"

# ==================== 注册命令 ====================
symmetric_cmd = on_command("对称", priority=10, block=True)

# ==================== 对称方向常量 ====================
VALID_DIRECTIONS = {"左", "右", "上", "下"}
DEFAULT_DIRECTION = "左"


# ==================== 图片处理核心 ====================
def _apply_symmetric(img: Image.Image, direction: str) -> Image.Image:
    """对单帧 RGBA 图片应用对称操作

    - 左：保留左半部分，水平镜像到右半
    - 右：保留右半部分，水平镜像到左半
    - 上：保留上半部分，垂直镜像到下半
    - 下：保留下半部分，垂直镜像到上半
    """
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
    """处理静态图片，输出 PNG 格式以保留透明通道"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    result = _apply_symmetric(img, direction)

    buf = BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _process_animated(img: Image.Image, direction: str) -> bytes:
    """处理动图（GIF / APNG / 动态 WebP），输出 GIF 格式

    流程：逐帧提取 → RGBA 对称处理 → 转 P 模式（保留透明）→ 合成 GIF
    """
    frames: list[Image.Image] = []
    durations: list[int] = []

    for frame in ImageSequence.Iterator(img):
        duration = frame.info.get("duration", 100)
        if duration <= 0:
            duration = 100
        durations.append(duration)

        rgba_frame = frame.convert("RGBA")
        processed = _apply_symmetric(rgba_frame, direction)
        frames.append(processed)

    if not frames:
        raise ValueError("动图中没有有效帧")

    # RGBA 帧转 P 模式，保留透明通道用于 GIF 输出
    gif_frames: list[Image.Image] = []
    for f in frames:
        alpha = f.split()[3]
        # 保留 255 个颜色，第 256 色(索引255)留给透明
        p_frame = f.convert("RGB").convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=255
        )
        # alpha <= 128 的像素标记为透明
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


# ==================== 辅助函数 ====================
def _extract_image_url(msg: Message) -> str | None:
    """从消息段列表中提取第一张图片的 URL"""
    for seg in msg:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                return url
    return None


async def _download_image(url: str, save_path: Path) -> None:
    """下载图片到本地临时路径"""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)


# ==================== 命令处理 ====================
@symmetric_cmd.handle()
async def handle_symmetric(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    """处理 /对称 命令"""

    # 1. 解析方向参数（默认"左"）
    text = args.extract_plain_text().strip()
    direction = DEFAULT_DIRECTION

    if text:
        first_char = text[0]
        if first_char in VALID_DIRECTIONS:
            direction = first_char

    # 2. 获取图片 URL（优先命令消息中的图片，其次回复消息中的图片）
    image_url = _extract_image_url(args)

    if not image_url and event.reply:
        image_url = _extract_image_url(event.reply.message)

    if not image_url:
        await symmetric_cmd.finish(
            "请附带图片或回复一张图片，例如：\n"
            "/对称 左 [图片]\n"
            "/对称 [图片]\n"
            "回复图片消息并发送 /对称 右"
        )

    # 3. 下载图片到临时目录
    temp_id = uuid.uuid4().hex
    temp_path = TEMP_DIR / temp_id

    try:
        await _download_image(image_url, temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        await symmetric_cmd.finish("图片下载失败，请稍后重试")

    # 4. 打开并处理图片
    try:
        with Image.open(temp_path) as img:
            is_animated = getattr(img, "is_animated", False)

            if is_animated:
                result_bytes = _process_animated(img, direction)
            else:
                result_bytes = _process_static(img, direction)
    except UnidentifiedImageError:
        temp_path.unlink(missing_ok=True)
        await symmetric_cmd.finish("无法识别的图片格式，请发送 PNG、JPG 或 GIF 图片")
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        await symmetric_cmd.finish(f"图片处理失败：{e}")

    # 5. 删除临时文件
    temp_path.unlink(missing_ok=True)

    # 6. 发送对称图片
    await symmetric_cmd.finish(MessageSegment.image(result_bytes))
