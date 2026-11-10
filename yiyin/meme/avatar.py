"""Avatar overlay using the original template dimensions and placement."""

from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..image.operations import ImageError, check_size

XNN_IMAGE_PATH = Path(__file__).resolve().parents[2] / "assets/images/meme/xnn.png"


def validate_avatar(data: bytes):
    try:
        with Image.open(BytesIO(data)) as source:
            check_size(source.size)
            source.load()
    except (OSError, ValueError, SyntaxError, EOFError,
            UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ImageError("无法读取头像，请稍后重试。") from error


def composite_avatar(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source, source.convert("RGBA") as base:
        check_size(base.size)
        with Image.open(XNN_IMAGE_PATH) as template, template.convert("RGBA") as overlay:
            with overlay.resize(base.size, Image.Resampling.LANCZOS) as scaled:
                # Preserve the original overlay placement and alpha mask.
                base.paste(scaled, (0, 0), scaled)
        with BytesIO() as output:
            base.save(output, format="PNG")
            return output.getvalue()
