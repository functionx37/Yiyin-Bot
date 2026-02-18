"""
可复用的 LLM API 调用模块
- 基于 OpenAI 兼容接口（云雾 API 中转站）
- 供其他插件调用，例如：
    from yiyin.llmapi import chat_completion
    reply = await chat_completion(messages, model="claude-haiku-4-5-20251001")
"""

import os
from typing import Any

import httpx

YUNWU_API_KEY: str = os.environ.get("YUNWU_API_KEY", "")
YUNWU_BASE_URL: str = os.environ.get("YUNWU_BASE_URL", "https://yunwu.ai/v1")


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.8,
    max_tokens: int = 256,
    top_p: float = 0.9,
    timeout: float = 30,
    **kwargs: Any,
) -> str | None:
    """调用 OpenAI 兼容的 Chat Completions 接口，返回助手回复文本。

    Args:
        messages: 对话消息列表，格式 [{"role": "...", "content": "..."}]
        model: 模型名称
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

        return choices[0].get("message", {}).get("content")

    except (httpx.TimeoutException, httpx.HTTPError, KeyError):
        return None
