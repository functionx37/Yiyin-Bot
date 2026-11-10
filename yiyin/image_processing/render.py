"""Static and animated image transforms used directly by the image plugin."""
from bisect import bisect_right
from io import BytesIO
from math import ceil, hypot

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 4_000_000
MAX_GIF_FRAMES = 100


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

SPIN_PERIOD_MS = 2000
SPIN_FRAME_MS = 50
SPIN_MAX_SIDE = 512
ANIMATED_ONLY = {'reverse', 'speed_up', 'slow_down'}
PARAMETERS = {
    'symmetric': {'左', '右', '上', '下'},
    'reverse': {''}, 'speed_up': {''}, 'slow_down': {''},
    'flip': {'左右', '上下'},
    'turn': {'90', '180', '270'},
    'spin': {'顺', '逆'},
}


def _size(width, height, spin):
    ratio = min(1.0, (MAX_IMAGE_PIXELS / (width * height)) ** 0.5)
    if spin:
        ratio = min(ratio, SPIN_MAX_SIDE / max(width, height))
    return max(1, int(width * ratio)), max(1, int(height * ratio))


def _duration(frame):
    value = frame.info.get('duration', 100)
    return max(10, round(float(value or 100) / 10) * 10)


def _speed_plan(durations, factor):
    scaled = [max(10, round(d / factor / 10) * 10) for d in durations]
    if max(scaled) > 655350:
        raise ValueError('变速后的单帧时长超过 GIF 格式上限，请使用更短的动图')
    if min(scaled) >= 20:
        return list(range(len(durations))), scaled
    # GIF players commonly clamp very short delays. Drop samples instead of
    # emitting sub-20ms frames that would play at the wrong speed.
    total = round(sum(durations) / factor / 10) * 10
    if total < 40:
        raise ValueError('这张动图已经很快，无法继续加速')
    boundaries = []
    elapsed = 0
    for duration in durations:
        elapsed += duration
        boundaries.append(elapsed)
    indices, output_durations = [], []
    for timestamp in range(0, total, 20):
        duration = min(20, total - timestamp)
        if duration < 20 and output_durations:
            output_durations[-1] += duration
            break
        index = min(len(durations) - 1, bisect_right(boundaries, timestamp * factor))
        indices.append(index)
        output_durations.append(duration)
    return indices, output_durations


def _spin_plan(durations):
    # Repeat whole input loops, preserving their order and timing. Align one
    # rotation to whole loops so both animation and angle wrap without a jump.
    repeats = max(1, min(MAX_GIF_FRAMES // len(durations), round(SPIN_PERIOD_MS / sum(durations))))
    segments = [(i, duration) for _ in range(repeats) for i, duration in enumerate(durations)]
    interval = max(SPIN_FRAME_MS, ceil(sum(d for _, d in segments) / MAX_GIF_FRAMES / 10) * 10)
    def parts(duration):
        return max(1, min(ceil(duration / interval), duration // 20))
    while sum(parts(d) for _, d in segments) > MAX_GIF_FRAMES:
        interval += 10
    plan = []
    elapsed, total = 0, sum(d for _, d in segments)
    for index, duration in segments:
        count = parts(duration)
        units, remainder = divmod(duration // 10, count)
        for part in range(count):
            delay = (units + (part < remainder)) * 10
            plan.append((index, 360 * elapsed / total, delay))
            elapsed += delay
    return plan


def _save_gif(frames, durations, loop):
    palettes = []
    try:
        for frame in frames:
            alpha = frame.getchannel('A')
            rgb = frame.convert('RGB')
            palette = rgb.quantize(colors=255)
            rgb.close()
            mask = alpha.point(lambda value: 255 if value <= 128 else 0)
            palette.paste(255, mask)
            palette.info['transparency'] = 255
            palettes.append(palette)
            alpha.close()
            mask.close()
        options = {} if loop is None else {'loop': loop}
        output = BytesIO()
        palettes[0].save(output, format='GIF', save_all=True,
                         append_images=palettes[1:], duration=durations,
                         disposal=2, transparency=255, background=255,
                         optimize=False, **options)
        return output.getvalue()
    finally:
        for frame in palettes:
            frame.close()


def _transform(frame, action, parameter):
    if action == 'symmetric':
        if (frame.width < 2 and parameter in {'左', '右'}) or (frame.height < 2 and parameter in {'上', '下'}):
            return frame.copy()
        return _apply_symmetric(frame, parameter)
    if action == 'flip':
        return frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT if parameter == '左右' else Image.Transpose.FLIP_TOP_BOTTOM)
    if action == 'turn':
        return frame.transpose({'90': Image.Transpose.ROTATE_270,
                                '180': Image.Transpose.ROTATE_180,
                                '270': Image.Transpose.ROTATE_90}[parameter])
    return frame.copy()


def process_image(source, action, parameter):
    if action not in PARAMETERS or parameter not in PARAMETERS[action]:
        raise ValueError('图片处理参数不正确，请发送 /图片处理 查看用法')
    frames, output_frames = [], []
    try:
        with Image.open(source) as image:
            first_frame = 1 if image.info.get('default_image', False) else 0
            count = getattr(image, 'n_frames', 1) - first_frame
            if count > MAX_GIF_FRAMES:
                raise ValueError(f'动图帧数过多（{count} 帧，上限 {MAX_GIF_FRAMES} 帧），请使用更短的动图')
            if action in ANIMATED_ONLY and count <= 1:
                raise ValueError('这个指令仅支持动图，请附带或回复一张动图')
            loop = image.info.get('loop')
            size = _size(*image.size, action == 'spin')
            durations = []
            for index in range(first_frame, first_frame + count):
                image.seek(index)
                # Pillow seek resolves GIF/APNG disposal/compositing before conversion.
                frame = image.convert('RGBA')
                # Animated WebP publishes this frame's duration only after load.
                durations.append(_duration(image))
                if count == 1:
                    oriented = ImageOps.exif_transpose(frame)
                    frame.close()
                    frame = oriented
                    size = _size(*frame.size, action == 'spin')
                if frame.size != size:
                    resized = frame.resize(size, Image.Resampling.LANCZOS)
                    frame.close()
                    frame = resized
                frames.append(frame)
        if action == 'reverse':
            return _save_gif(list(reversed(frames)), list(reversed(durations)), loop)
        if action in {'speed_up', 'slow_down'}:
            indices, delays = _speed_plan(durations, 2 if action == 'speed_up' else 0.5)
            return _save_gif([frames[i] for i in indices], delays, loop)
        if action == 'spin':
            if count == 1:
                durations = [SPIN_PERIOD_MS]
            plan = _spin_plan(durations)
            side = ceil(hypot(*frames[0].size)) + 2
            for index, angle, _ in plan:
                canvas = Image.new('RGBA', (side, side))
                frame = frames[index]
                canvas.paste(frame, ((side-frame.width)//2, (side-frame.height)//2))
                rotated = canvas.rotate(-angle if parameter == '顺' else angle,
                                        resample=Image.Resampling.BICUBIC)
                canvas.close()
                output_frames.append(rotated)
            return _save_gif(output_frames, [delay for _, _, delay in plan], 0)
        output_frames = [_transform(frame, action, parameter) for frame in frames]
        if count > 1:
            return _save_gif(output_frames, durations, loop)
        output = BytesIO()
        output_frames[0].save(output, format='PNG')
        return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError('无法识别图片或图片尺寸过大，请发送 PNG、JPG、GIF、WebP 图片') from error
    finally:
        for frame in frames + output_frames:
            frame.close()
