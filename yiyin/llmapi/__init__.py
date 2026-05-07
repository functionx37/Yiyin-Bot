"""
可复用的 LLM API 调用模块
- 基于 OpenAI 兼容接口，默认读取通用环境变量 LLM_BASE_URL / LLM_API_KEY
- 兼容旧版 YUNWU_BASE_URL / YUNWU_API_KEY，便于平滑迁移
- 支持纯文本对话、识图（Vision）多模态输入、参考图生成图片
- 供其他插件调用，例如：
    from yiyin.llmapi import chat_completion, describe_image, generate_image_edit
    reply = await chat_completion(messages, model="claude-haiku-4-5-20251001")
    desc = await describe_image("描述这张图", "https://example.com/img.png")
    imgs = await generate_image_edit("把背景换成星空", "https://example.com/photo.png")
"""

import base64
import os
from pathlib import Path
from typing import Any

import httpx

from nonebot.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DOTENV_LOADED = False


class ChatCompletionError(RuntimeError):
    """Chat completion 调用失败。"""


class ChatCompletionTransportError(ChatCompletionError):
    """Chat completion 在网络传输阶段失败。"""


def _get_env_value(primary_name: str, legacy_name: str, default: str = "") -> str:
    """优先读取新的通用变量名，缺失时回退到旧变量名。"""
    return os.environ.get(primary_name) or os.environ.get(legacy_name) or default


def _get_api_key() -> str:
    """获取 LLM_API_KEY，若为空则尝试加载 .env.prod 后重试。"""
    global _DOTENV_LOADED
    key = _get_env_value("LLM_API_KEY", "YUNWU_API_KEY")
    if not key and not _DOTENV_LOADED:
        try:
            from dotenv import load_dotenv
            for f in (".env.prod", ".env"):
                p = _PROJECT_ROOT / f
                if p.exists():
                    load_dotenv(p)
                    _DOTENV_LOADED = True
                    key = _get_env_value("LLM_API_KEY", "YUNWU_API_KEY")
                    if key:
                        logger.info("llmapi: 已从 {} 加载 LLM_API_KEY", f)
                    break
        except ImportError:
            pass
        _DOTENV_LOADED = True
    return key


def _get_base_url() -> str:
    return _get_env_value("LLM_BASE_URL", "YUNWU_BASE_URL", "https://yunwu.ai/v1")


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


def _build_data_url(image_bytes: bytes, content_type: str | None) -> str:
    """将图片二进制转为 data URL，统一以内联方式发给模型。"""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_http_timeout(timeout: float) -> httpx.Timeout:
    """为上传图片场景提供更宽松的 write 超时，避免大图 base64 上传超时。"""
    base = max(float(timeout), 1.0)
    return httpx.Timeout(
        connect=min(base, 15.0),
        read=base,
        write=max(base * 2, 180.0),
        pool=min(base, 15.0),
    )


async def _download_image_as_data_url(
    image_url: str,
    *,
    timeout: float = 30,
) -> str | None:
    """下载图片后转为 data URL，避免把外链直接交给模型。"""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_build_http_timeout(timeout),
        ) as client:
            resp = await client.get(image_url)
        resp.raise_for_status()
        return _build_data_url(resp.content, resp.headers.get("content-type"))
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        logger.warning("下载图片并转 data URL 失败: {}: {}", type(e).__name__, e)
        return None


def _summarize_messages(messages: list[dict[str, Any]]) -> dict[str, int]:
    """汇总消息体，便于失败时快速判断是否包含图片及大致规模。"""
    summary = {
        "messages": len(messages),
        "text_chars": 0,
        "inline_images": 0,
        "remote_images": 0,
    }
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            summary["text_chars"] += len(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    summary["text_chars"] += len(text)
            elif part.get("type") == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str) and url.startswith("data:"):
                    summary["inline_images"] += 1
                elif url:
                    summary["remote_images"] += 1
    return summary


async def _inline_image_urls(
    messages: list[dict[str, Any]],
    *,
    timeout: float,
) -> tuple[list[dict[str, Any]], int, int]:
    """将外链图片统一转为 data URL，尽量避免模型侧再次拉图。"""
    converted = 0
    failed = 0
    prepared_messages: list[dict[str, Any]] = []

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            prepared_messages.append(msg)
            continue

        changed = False
        new_content: list[Any] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                new_content.append(part)
                continue

            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str) or not url or url.startswith("data:"):
                new_content.append(part)
                continue

            data_url = await _download_image_as_data_url(url, timeout=timeout)
            if not data_url:
                failed += 1
                new_content.append(part)
                continue

            converted += 1
            changed = True
            new_part = dict(part)
            if isinstance(image_url, dict):
                new_image_url = dict(image_url)
                new_image_url["url"] = data_url
                new_part["image_url"] = new_image_url
            else:
                new_part["image_url"] = {"url": data_url}
            new_content.append(new_part)

        if changed:
            new_msg = dict(msg)
            new_msg["content"] = new_content
            prepared_messages.append(new_msg)
        else:
            prepared_messages.append(msg)

    return prepared_messages, converted, failed


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.8,
    max_tokens: int = 256,
    top_p: float = 0.9,
    timeout: float = 30,
    raise_on_error: bool = False,
    **kwargs: Any,
) -> str | None:
    """调用 OpenAI 兼容的 Chat Completions 接口，返回助手回复文本。

    支持纯文本与识图（Vision）多模态输入。识图时，messages 中 content 可为数组：
    [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]

    兼容任意 OpenAI 风格网关；默认示例地址为云雾 API：https://yunwu.apifox.cn/

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
        logger.warning(
            "LLM_API_KEY 未配置，跳过 LLM 请求。请在 .env.prod 中设置 LLM_API_KEY"
        )
        return None

    prepared_messages, converted_images, failed_image_inline = await _inline_image_urls(
        messages,
        timeout=timeout,
    )
    summary = _summarize_messages(prepared_messages)

    if failed_image_inline:
        logger.warning(
            "chat_completion 有图片未能转为 data URL，将回退为外链传图: model={} timeout={} summary={} failed_images={}",
            model,
            timeout,
            summary,
            failed_image_inline,
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": prepared_messages,
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
        async with httpx.AsyncClient(timeout=_build_http_timeout(timeout)) as client:
            resp = await client.post(
                f"{_get_base_url()}/chat/completions",
                json=payload,
                headers=headers,
            )

        if resp.status_code != 200:
            logger.warning(
                "chat_completion 非 200: status={} model={} timeout={} summary={} converted_images={} body={}",
                resp.status_code,
                model,
                timeout,
                summary,
                converted_images,
                resp.text[:200],
            )
            return None

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            logger.warning(
                "chat_completion choices 为空: model={} timeout={} summary={} converted_images={} body={}",
                model,
                timeout,
                summary,
                converted_images,
                {k: v for k, v in data.items() if k != "usage"},
            )
            return None

        first = choices[0]
        msg = first.get("message", {})
        content = msg.get("content")
        result = _extract_text_from_content(content) or (
            content if isinstance(content, str) else None
        )
        if result is None:
            logger.warning(
                "chat_completion 成功但 content 为空: model={} timeout={} summary={} converted_images={} finish_reason={} content_type={} content={}",
                model,
                timeout,
                summary,
                converted_images,
                first.get("finish_reason"),
                type(content).__name__,
                repr(content)[:100] if content is not None else None,
            )
        return result

    except (httpx.TimeoutException, httpx.HTTPError, KeyError) as e:
        logger.warning(
            "chat_completion 异常: {}: {} | model={} timeout={} summary={} converted_images={}",
            type(e).__name__,
            e,
            model,
            timeout,
            summary,
            converted_images,
        )
        if raise_on_error:
            raise ChatCompletionTransportError(f"{type(e).__name__}: {e}") from e
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

    OpenAI 兼容 Vision 接口；云雾示例文档：https://yunwu.apifox.cn/

    Args:
        prompt: 对图片的提问或指令（如『简短描述这张图』）
        image_url: 图片 URL，会先下载后转成 data URL 再发给模型
        model: Vision 模型，默认 gpt-4o-mini
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）

    Returns:
        模型对图片的理解/描述文本，失败时返回 None
    """
    data_url = await _download_image_as_data_url(image_url, timeout=timeout)
    if not data_url:
        return None

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
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


def _resolve_image_bytes(source: str | bytes) -> bytes:
    """将图片来源统一转为 bytes。支持 base64 data-url、普通 bytes。"""
    if isinstance(source, bytes):
        return source
    if isinstance(source, str) and source.startswith("data:"):
        header, _, b64_data = source.partition(",")
        return base64.b64decode(b64_data)
    raise ValueError(f"不支持的图片来源类型: {type(source)}")


async def generate_image_edit(
    prompt: str,
    image_sources: str | bytes | list[str | bytes],
    *,
    model: str = "gpt-image-1",
    size: str = "1024x1024",
    quality: str = "auto",
    n: int = 1,
    output_format: str = "png",
    timeout: float = 120,
) -> list[bytes] | None:
    """使用参考图 + 文字描述生成新图片，返回图片二进制数据列表。

    基于 OpenAI 兼容的 Images Edits 接口（POST /v1/images/edits，multipart/form-data）。
    云雾示例文档：https://yunwu.apifox.cn/

    gpt-image-1 支持最多 16 张参考图，模型会综合参考图内容与文字描述来生成。

    Args:
        prompt: 文字描述 / 编辑指令（如"把背景换成星空"、"融合这两张图的风格"）
        image_sources: 图片数据，支持以下格式（单个或列表）：
                       - bytes: 图片二进制数据
                       - str: base64 data-url（"data:image/png;base64,..."）
        model: 图片模型，默认 gpt-image-1；也可用 gpt-image-1.5 等
        size: 输出尺寸 "auto" | "1024x1024" | "1536x1024" | "1024x1536"
        quality: 输出质量 "low" | "medium" | "high" | "auto"
        n: 生成数量（1-4）
        output_format: 输出格式 "png" | "jpeg" | "webp"
        timeout: 请求超时（秒），图片生成通常较慢，默认 120s

    Returns:
        图片二进制数据列表（可直接写入文件或发送），失败时返回 None
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning(
            "LLM_API_KEY 未配置，跳过图片生成请求。请在 .env.prod 中设置 LLM_API_KEY"
        )
        return None

    if not isinstance(image_sources, list):
        image_sources = [image_sources]

    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for i, src in enumerate(image_sources):
        img_bytes = _resolve_image_bytes(src)
        files.append(("image[]", (f"image_{i}.png", img_bytes, "image/png")))

    data: dict[str, str] = {
        "model": model,
        "prompt": prompt,
        "n": str(n),
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{_get_base_url()}/images/edits",
                data=data,
                files=files,
                headers=headers,
            )

        if resp.status_code != 200:
            logger.warning(
                "generate_image_edit 非 200: status={} body={}",
                resp.status_code,
                resp.text[:500],
            )
            return None

        resp_data = resp.json()
        items: list[dict[str, Any]] = resp_data.get("data", [])
        if not items:
            logger.warning("generate_image_edit data 为空: {}", resp_data)
            return None

        results: list[bytes] = []
        for item in items:
            b64 = item.get("b64_json")
            if b64:
                results.append(base64.b64decode(b64))
            else:
                url = item.get("url")
                if url:
                    async with httpx.AsyncClient(timeout=30) as dl:
                        img_resp = await dl.get(url)
                        if img_resp.status_code == 200:
                            results.append(img_resp.content)
                        else:
                            logger.warning("下载生成图片失败: status={} url={}", img_resp.status_code, url)

        return results if results else None

    except (httpx.TimeoutException, httpx.HTTPError, KeyError, Exception) as e:
        logger.warning("generate_image_edit 异常: {}: {}", type(e).__name__, e)
        return None


def _extract_images_from_content(content: Any) -> list[bytes] | None:
    """从 chat completions 响应的 content 中提取图片二进制数据。

    支持多种代理返回格式：
    - content 为 list，其中 image_url 类型含 data-url
    - content 为 list，其中 inline_data 类型含 base64
    - content 为 str，内含 data-url
    """
    if content is None:
        return None

    import re

    results: list[bytes] = []

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                url = ""
                iu = part.get("image_url")
                if isinstance(iu, dict):
                    url = iu.get("url", "")
                elif isinstance(iu, str):
                    url = iu
                if url.startswith("data:"):
                    _, _, b64_data = url.partition(",")
                    try:
                        results.append(base64.b64decode(b64_data))
                    except Exception:
                        pass
            elif part.get("type") == "inline_data":
                b64_data = part.get("inline_data", {}).get("data", "")
                if b64_data:
                    try:
                        results.append(base64.b64decode(b64_data))
                    except Exception:
                        pass

    elif isinstance(content, str):
        for m in re.finditer(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content):
            try:
                results.append(base64.b64decode(m.group(1)))
            except Exception:
                pass

    return results if results else None


async def generate_image_via_chat(
    prompt: str,
    image_sources: str | bytes | list[str | bytes] | None = None,
    *,
    model: str = "gemini-2.0-flash-exp-image-generation",
    max_tokens: int = 4096,
    timeout: float = 420,
) -> list[bytes] | None:
    """使用 Chat Completions 接口生成 / 编辑图片（Gemini 等原生多模态模型）。

    通过 /v1/chat/completions 发送多模态消息（文本 + base64 图片），
    从响应 content 中提取生成的图片。适用于支持该兼容格式的 OpenAI 风格网关。

    Args:
        prompt: 文字描述 / 编辑指令
        image_sources: 参考图片（可选），支持 bytes 或 base64 data-url，单个或列表
        model: 模型名称
        max_tokens: 最大生成 token 数
        timeout: 请求超时（秒）

    Returns:
        图片二进制数据列表，失败时返回 None
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning(
            "LLM_API_KEY 未配置，跳过图片生成请求。请在 .env.prod 中设置 LLM_API_KEY"
        )
        return None

    if image_sources is not None and not isinstance(image_sources, list):
        image_sources = [image_sources]

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

    if image_sources:
        for src in image_sources:
            img_bytes = _resolve_image_bytes(src)
            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64_str}"},
            })

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "stream": False,
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
            logger.warning(
                "generate_image_via_chat 非 200: status={} body={}",
                resp.status_code,
                resp.text[:500],
            )
            return None

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            logger.warning("generate_image_via_chat choices 为空: {}", data)
            return None

        msg_content = choices[0].get("message", {}).get("content")
        results = _extract_images_from_content(msg_content)
        if not results:
            logger.warning(
                "generate_image_via_chat 未在响应中找到图片: content_type={}, content={}",
                type(msg_content).__name__,
                repr(msg_content)[:200] if msg_content is not None else None,
            )
        return results

    except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
        logger.warning("generate_image_via_chat 异常: {}: {}", type(e).__name__, e)
        return None
