"""Keep Pilmoji's original emoji style, with bounded network reads and cache."""

from functools import lru_cache
from time import monotonic
from urllib.request import Request, urlopen

from pilmoji.source import TwitterEmojiSource

from ..image.operations import ImageError


@lru_cache(maxsize=64)
def fetch_emoji(url: str) -> bytes:
    try:
        with urlopen(Request(url, headers={"User-Agent": "Yiyin-Bot"}), timeout=5) as response:
            data = response.read(512_001)
        if not data or len(data) > 512_000:
            raise ValueError("Invalid emoji size")
        return data
    except Exception as error:
        raise ImageError("表情资源下载失败，请稍后重试或去掉 emoji。") from error


class BoundedEmojiSource(TwitterEmojiSource):
    def __init__(self):
        super().__init__()
        self.deadline = monotonic() + 15

    def request(self, url: str) -> bytes:
        if monotonic() > self.deadline:
            raise ImageError("表情资源下载超时，请减少不同 emoji 的数量后重试。")
        return fetch_emoji(url)
