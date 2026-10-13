"""聊天截图通过与其他图片任务共用的资源保护生成。"""
from resource_guard import render_bytes


async def async_generate_chat_screenshot(avatar_bytes: bytes, nickname: str, text: str) -> bytes:
    return await render_bytes('chat', {'avatar_bytes': avatar_bytes, 'nickname': nickname, 'text': text})