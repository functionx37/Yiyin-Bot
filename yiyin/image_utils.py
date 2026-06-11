from io import BytesIO

from nonebot.log import logger
from PIL import Image, UnidentifiedImageError

_COMPRESS_TRIGGER_BYTES = 10 * 1024 * 1024
_COMPRESS_TARGET_BYTES = 9 * 1024 * 1024
_PNG_COMPRESS_PLANS: tuple[tuple[float, int | None], ...] = (
    (1.0, None),
    (1.0, 256),
    (1.0, 224),
    (0.96, 256),
    (0.92, 224),
    (0.88, 192),
    (0.85, 160),
)


def _normalize_image_mode(image: Image.Image) -> Image.Image:
    has_alpha = "A" in image.getbands()
    target_mode = "RGBA" if has_alpha else "RGB"
    if image.mode == target_mode:
        return image.copy()
    return image.convert(target_mode)


def _resize_image(image: Image.Image, scale: float) -> Image.Image:
    if scale >= 0.999:
        return image.copy()
    width = max(1, int(round(image.width * scale)))
    height = max(1, int(round(image.height * scale)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _quantize_for_png(image: Image.Image, colors: int) -> Image.Image:
    if image.mode == "RGBA":
        return image.quantize(
            colors=colors,
            method=Image.Quantize.FASTOCTREE,
            dither=Image.Dither.NONE,
        )
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    return rgb.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )


def _save_png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def maybe_compress_large_png(
    image_bytes: bytes,
    content_type: str | None = None,
    *,
    log_prefix: str = "图片压缩",
) -> tuple[bytes, str | None, bool]:
    if len(image_bytes) <= _COMPRESS_TRIGGER_BYTES:
        return image_bytes, content_type, False

    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            opened.load()
            source = _normalize_image_mode(opened)
    except (UnidentifiedImageError, OSError) as e:
        logger.warning("{} 跳过压缩，无法识别图片: {}", log_prefix, e)
        return image_bytes, content_type, False
    except Exception:
        logger.exception("{} 跳过压缩时出现异常", log_prefix)
        return image_bytes, content_type, False

    best_bytes = image_bytes
    compressed = False
    for scale, colors in _PNG_COMPRESS_PLANS:
        working = _resize_image(source, scale)
        if colors is not None:
            working = _quantize_for_png(working, colors)
        candidate = _save_png_bytes(working)
        if len(candidate) < len(best_bytes):
            best_bytes = candidate
            compressed = True
        if len(candidate) <= _COMPRESS_TARGET_BYTES:
            best_bytes = candidate
            compressed = True
            break

    if compressed:
        logger.info(
            "{} 大图压缩完成: {:.2f}MB -> {:.2f}MB",
            log_prefix,
            len(image_bytes) / 1024 / 1024,
            len(best_bytes) / 1024 / 1024,
        )
        return best_bytes, "image/png", True

    logger.info(
        "{} 已尝试压缩，但体积未降低: {:.2f}MB",
        log_prefix,
        len(image_bytes) / 1024 / 1024,
    )
    return image_bytes, content_type, False
