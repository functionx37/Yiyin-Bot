"""
可复用的 LLM API 调用模块
- 基于 OpenAI 兼容接口（云雾 API 中转站，https://yunwu.apifox.cn/）
- 支持纯文本对话与识图（Vision）多模态输入
- 供其他插件调用，例如：
    from yiyin.llmapi import chat_completion, describe_image
    reply = await chat_completion(messages, model="claude-haiku-4-5-20251001")
    desc = await describe_image("描述这张图", "https://example.com/img.png")
"""

import os
from typing import Any

import httpx

YUNWU_API_KEY: str = os.environ.get("YUNWU_API_KEY", "")
YUNWU_BASE_URL: str = os.environ.get("YUNWU_BASE_URL", "https://yunwu.ai/v1")


def _extract_text_from_content(content: Any) -> str | None:
    """从 message.content 提取文本。content 可能为 str 或 list（多模态返回）。"""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                if "text" in part:
                    texts.append(part["text"])
        return "\n".join(texts) if texts else None
    return None


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.8,
    max_tokens: int = 256,
    top_p: float = 0.9,
    timeout: float = 30,
    **kwargs: Any,
) -> str | None:
    """调用 OpenAI 兼容的 Chat Completions 接口，返回助手回复文本。

    支持纯文本与识图（Vision）多模态输入。识图时，messages 中 content 可为数组：
    [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"https://..."}}]

    云雾 API 文档：https://yunwu.apifox.cn/

    Args:
        messages: 对话消息列表。content 可为 str 或 list（多模态，含 image_url）
        model: 模型名称（识图需用 Vision 模型，如 gpt-4o-mini、gpt-4o）
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        top_p: 核采样概率
        timeout: 请求超时（秒）

    Returns:
        助手回复文本，失败时返回 None
    """
    if not YUNWU_API_KEY:
        return None

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "stream": False,
        **kwargs,
    }

    headers = {
        "Authorization": f"Bearer {YUNWU_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{YUNWU_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )

        if resp.status_code != 200:
            return None

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return None

        content = choices[0].get("message", {}).get("content")
        return _extract_text_from_content(content) or (
            content if isinstance(content, str) else None
        )

    except (httpx.TimeoutException, httpx.HTTPError, KeyError):
        return None


async def describe_image(
    prompt: str,
    image_url: str,
    *,
    model: str = "gpt-4o-mini",
    max_tokens: int = 256,
    timeout: float = 30,
) -> str | None:
    """使用 Vision 模型描述/理解图片，返回文本描述。

    云雾 API 识图文档：https://yunwu.apifox.cn/ （创建聊天识图）

    Args:
        prompt: 对图片的提问或指令（如「简短描述这张图」）
        image_url: 图片 URL，需公网可访问（支持 jpeg/png/gif/webp）
        model: Vision 模型，默认 gpt-4o-mini
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）

    Returns:
        模型对图片的理解/描述文本，失败时返回 None
    """
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    return await chat_completion(
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=0.3,
        timeout=timeout,
    )
