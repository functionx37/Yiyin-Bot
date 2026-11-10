"""Bounded Pillow transforms. Static output is RGBA PNG; animations are GIF."""

import math
from dataclasses import dataclass
from io import BytesIO
from struct import error as StructError

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 10_000_000
MAX_SIDE = 4096
MAX_PIXELS = 4_000_000
MAX_FRAMES = 120
MAX_TOTAL_PIXELS = 24_000_000
SPIN_STEP_MS = 50


class ImageError(Exception):
    """An actionable error safe to show to the caller."""


@dataclass(frozen=True)
class Operation:
    name: str
    argument: str = ""


@dataclass(frozen=True)
class Result:
    data: bytes
    filename: str


def check_size(size: tuple[int, int], frames: int = 1):
    width, height = size
    if width <= 0 or height <= 0 or max(size) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageError("图片过大：单帧最多 400 万像素，单边不超过 4096 像素。")
    if frames > MAX_FRAMES:
        raise ImageError("动图帧数过多：最多处理 120 帧（含生成的帧）。")
    if width * height * frames > MAX_TOTAL_PIXELS:
        raise ImageError("图片累计像素过多：所有帧合计最多 2400 万像素（含生成的帧）。")


def mirror(frame: Image.Image, direction: str) -> Image.Image:
    """Mirror one half; an odd-sized centre row/column appears exactly once."""
    width, height = frame.size
    result = frame.copy()
    if direction in {"左", "右"}:
        half = width // 2
        box = (0, 0, half, height) if direction == "左" else (width - half, 0, width, height)
        position = (width - half, 0) if direction == "左" else (0, 0)
        transpose = Image.Transpose.FLIP_LEFT_RIGHT
    else:
        half = height // 2
        box = (0, 0, width, half) if direction == "上" else (0, height - half, width, height)
        position = (0, height - half) if direction == "上" else (0, 0)
        transpose = Image.Transpose.FLIP_TOP_BOTTOM
    if half:
        with frame.crop(box) as cropped, cropped.transpose(transpose) as reflected:
            # Pasting without a mask copies alpha too, including fully clear pixels.
            result.paste(reflected, position)
    return result


def transform(frame: Image.Image, operation: Operation) -> Image.Image:
    if operation.name == "对称":
        return mirror(frame, operation.argument)
    if operation.name == "翻转":
        return frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT if operation.argument == "左右"
                               else Image.Transpose.FLIP_TOP_BOTTOM)
    if operation.name == "转":
        transpose = {"90": Image.Transpose.ROTATE_270, "180": Image.Transpose.ROTATE_180,
                     "270": Image.Transpose.ROTATE_90}[operation.argument]
        return frame.transpose(transpose)
    return frame.copy()


def rotated(frame: Image.Image, degrees: float, side: int) -> Image.Image:
    """Rotate around the exact geometric centre onto a fixed, unclipped canvas."""
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    # Inverse transform from output pixel centres to input coordinates. Positive
    # angles move clockwise in image coordinates; no integer-paste centre jitter.
    centre = side / 2
    coefficients = (cosine, sine, frame.width / 2 - centre * (cosine + sine),
                    -sine, cosine, frame.height / 2 + centre * (sine - cosine))
    return frame.transform((side, side), Image.Transform.AFFINE, coefficients,
                           resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0))


def gif_frame(frame: Image.Image) -> Image.Image:
    """Reserve index 0 for transparency so opaque black never becomes clear.

    GIF only supports binary alpha; threshold partial alpha at 128. RGBA PNG
    output keeps all 256 alpha levels. Disposal=2 prevents transparent trails.
    """
    with frame.convert("RGB") as rgb:
        quantized = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette = (quantized.getpalette() or [])[:765]
    shifted = quantized.point([index + 1 for index in range(256)])
    quantized.close()
    shifted.putpalette(([0, 0, 0] + palette + [0] * 768)[:768])
    with frame.getchannel("A") as alpha, alpha.point(lambda value: 255 if value < 128 else 0) as mask:
        shifted.paste(0, mask=mask)
    shifted.info["transparency"] = 0
    return shifted


class LimitedOutput(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_FILE_BYTES:
            raise ImageError("处理后的图片超过 10 MB，请使用较小的图片。")
        return super().write(data)


def frame_duration(value) -> int:
    duration = round(float(value or 100))
    if not 0 < duration <= 600_000:
        raise ImageError("动图帧时长异常，无法处理。")
    return duration


def process(data: bytes, operation: Operation) -> Result:
    if len(data) > MAX_FILE_BYTES:
        raise ImageError("图片文件过大：最多 10 MB。")
    frames: list[Image.Image] = []
    encoded: list[Image.Image] = []
    try:
        durations = []
        with Image.open(BytesIO(data), formats=("PNG", "JPEG", "GIF", "WEBP", "BMP")) as source:
            check_size(source.size)
            loop = source.info.get("loop")
            # APNG's optional default image is a poster, not an animation frame.
            start = 1 if source.info.get("default_image") else 0
            index = start
            while True:
                try:
                    source.seek(index)
                except EOFError:
                    break
                check_size(source.size, len(frames) + 1)
                source.load()  # WEBP exposes frame duration after decoding.
                with source.convert("RGBA") as rgba:
                    frames.append(ImageOps.exif_transpose(rgba))
                durations.append(frame_duration(source.info.get("duration")))
                index += 1

        if not frames:
            raise ImageError("图片中没有可读取的帧。")
        animated = len(frames) > 1
        if operation.name in {"倒放", "加速", "减速"} and not animated:
            raise ImageError(f"/{operation.name} 需要动图，请发送或引用一张动图。")
        if operation.name == "倒放":
            frames.reverse()
            durations.reverse()
        elif operation.name in {"加速", "减速"}:
            factor = 0.5 if operation.name == "加速" else 2
            durations = [duration * factor for duration in durations]

        if operation.name == "旋转":
            if not animated:
                durations = [2000]
            side = math.ceil(math.hypot(*frames[0].size))
            counts = [max(1, math.ceil(duration / SPIN_STEP_MS)) for duration in durations]
            check_size((side, side), sum(counts))
            total = sum(durations)
            elapsed = 0
            output_durations = []
            sign = 1 if operation.argument == "顺" else -1
            for frame, duration, count in zip(frames, durations, counts):
                for step in range(count):
                    angle = sign * 360 * (elapsed + step * duration / count) / total
                    with rotated(frame, angle, side) as transformed:
                        encoded.append(gif_frame(transformed))
                    output_durations.append(duration / count)
                elapsed += duration
            durations = output_durations
            animated, loop = True, 0
        elif animated:
            for frame in frames:
                with transform(frame, operation) as transformed:
                    encoded.append(gif_frame(transformed))
        else:
            with transform(frames[0], operation) as transformed, LimitedOutput() as output:
                transformed.save(output, format="PNG")
                return Result(output.getvalue(), "image.png")

        # Quantize the timeline, rather than each delay, to avoid accumulated
        # rounding drift. Sub-10ms frames are sampled out, rather than slowing
        # the whole animation back down after a 2x speed-up.
        selected, delays, elapsed, previous = [], [], 0.0, 0
        for frame, duration in zip(encoded, durations):
            elapsed += duration
            tick = math.floor(elapsed / 10 + 0.5)
            if tick <= previous:
                continue
            delay = (tick - previous) * 10
            if delay > 655_350:
                raise ImageError("处理后的动图帧时长过长，无法保存为 GIF。")
            selected.append(frame)
            delays.append(delay)
            previous = tick
        if not selected:
            selected, delays = [encoded[0]], [10]
        with LimitedOutput() as output:
            settings = {"loop": loop} if loop is not None else {}
            selected[0].save(output, format="GIF", save_all=True, append_images=selected[1:],
                            duration=delays, disposal=2, transparency=0, background=0,
                            optimize=False, **settings)
            return Result(output.getvalue(), "image.gif")
    except ImageError:
        raise
    except StructError as error:
        raise ImageError("动图帧时长过长，无法保存为 GIF，请使用播放时长较短的动图。") from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
            OverflowError, Image.DecompressionBombError) as error:
        raise ImageError("无法读取这张图片：文件损坏或格式不支持，请使用 PNG、JPEG、GIF、WebP 或 BMP。") from error
    except MemoryError as error:
        raise ImageError("图片占用内存过多，请使用较小的图片或减少动图帧数。") from error
    finally:
        for frame in frames + encoded:
            frame.close()
