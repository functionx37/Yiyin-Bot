"""Standalone image worker: no NoneBot imports, disposable per request."""
import sys
import resource
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageSequence, UnidentifiedImageError

# Preserve existing format limits; RAM admission is computed per request.
MAX_IMAGE_PIXELS = 4_000_000
MAX_GIF_FRAMES = 100

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
    gif_frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(img):
        duration = frame.info.get("duration", 100)
        if duration <= 0:
            duration = 100
        durations.append(duration)
        rgba_frame = frame.convert("RGBA")
        rgba_frame = _downscale_if_needed(rgba_frame)
        processed = _apply_symmetric(rgba_frame, direction)
        alpha = processed.getchannel("A")
        p_frame = processed.convert("RGB").convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=255
        )
        mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
        p_frame.paste(255, mask)
        p_frame.info["transparency"] = 255
        gif_frames.append(p_frame)
        alpha.close()
        mask.close()
        processed.close()
        if rgba_frame is not frame:
            rgba_frame.close()
    if not gif_frames:
        raise ValueError("动图中没有有效帧")
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



def estimate_memory(width, height, frames, file_size):
    pixels = width * height
    scaled = min(pixels, MAX_IMAGE_PIXELS)
    # Decoder/source buffers, transforms, retained palette frames, GIF encoder
    # copies and compressed input/output. Conservative admission estimate.
    return 48 * 1024**2 + pixels * 16 + scaled * (16 + frames * 4) + file_size * 4


