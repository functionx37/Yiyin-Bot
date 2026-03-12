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
from pathlib import Path
from typing import Any

import httpx

from nonebot.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DOTENV_LOADED = False


def _get_api_key() -> str:
    """获取 YUNWU_API_KEY，若为空则尝试加载 .env.prod 后重试（解决 nb run 子进程可能未读到的情况）"""
    global _DOTENV_LOADED
    key = os.environ.get("YUNWU_API_KEY", "")
    if not key and not _DOTENV_LOADED:
        try:
            from dotenv import load_dotenv
            for f in (".env.prod", ".env"):
                p = _PROJECT_ROOT / f
                if p.exists():
                    load_dotenv(p)
                    _DOTENV_LOADED = True
                    key = os.environ.get("YUNWU_API_KEY", "")
                    if key:
                        logger.info("llmapi: 已从 %s 加载 YUNWU_API_KEY", f)
                    break
        except ImportError:
            pass
        _DOTENV_LOADED = True
    return key


def _get_base_url() -> str:
    return os.environ.get("YUNWU_BASE_URL", "https://yunwu.ai/v1")


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
    api_key = _get_api_key()
    if not api_key:
        logger.warning("YUNWU_API_KEY 未配置，跳过 LLM 请求。请在 .env.prod 中设置 YUNWU_API_KEY")
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
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{_get_base_url()}/chat/completions",
                json=payload,
                headers=headers,
            )

        if resp.status_code != 200:
            logger.warning("chat_completion 非 200: status=%s body=%s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            logger.warning("chat_completion choices 为空: %s", {k: v for k, v in data.items() if k != "usage"})
            return None

        first = choices[0]
        msg = first.get("message", {})
        content = msg.get("content")
        result = _extract_text_from_content(content) or (
            content if isinstance(content, str) else None
        )
        if result is None:
            logger.warning(
                "chat_completion 成功但 content 为空: finish_reason=%s, content_type=%s, content=%s",
                first.get("finish_reason"),
                type(content).__name__,
                repr(content)[:100] if content is not None else None,
            )
        return result

    except (httpx.TimeoutException, httpx.HTTPError, KeyError) as e:
        logger.warning("chat_completion 异常: %s: %s", type(e).__name__, e)
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
        prompt: 对图片的提问或指令（如『简短描述这张图』）
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
