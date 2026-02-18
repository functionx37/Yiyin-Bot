"""
NoneBot2 翻译插件
- 命令：/翻译 <目标语言> [文本]
- 功能：调用腾讯云机器翻译 API，支持中/英/日互译
- 同时对外暴露 translate_text() 供其他插件调用
"""

import os
import json
import hashlib
import hmac
import time
from datetime import datetime, timezone

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

# ==================== 配置 ====================
TENCENT_SECRET_ID: str = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY: str = os.environ.get("TENCENT_SECRET_KEY", "")
TMT_REGION = "ap-guangzhou"
TMT_ENDPOINT = "tmt.tencentcloudapi.com"
TMT_SERVICE = "tmt"
TMT_VERSION = "2018-03-21"
TMT_ACTION = "TextTranslate"

LANG_MAP: dict[str, str] = {
    "中文": "zh",
    "中": "zh",
    "zh": "zh",
    "英文": "en",
    "英": "en",
    "en": "en",
    "日文": "ja",
    "日": "ja",
    "日语": "ja",
    "ja": "ja",
}

LANG_DISPLAY: dict[str, str] = {
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
}

# ==================== 腾讯云 TC3 签名 ====================


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _build_auth_header(payload: str, timestamp: int) -> dict[str, str]:
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    # 1. 拼接规范请求串
    canonical_request = (
        "POST\n"
        "/\n"
        "\n"
        f"content-type:application/json; charset=utf-8\n"
        f"host:{TMT_ENDPOINT}\n"
        f"x-tc-action:{TMT_ACTION.lower()}\n"
        "\n"
        "content-type;host;x-tc-action\n"
        + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    )

    # 2. 拼接待签名字符串
    credential_scope = f"{date}/{TMT_SERVICE}/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        + hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    )

    # 3. 计算签名
    secret_date = _sign(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date)
    secret_service = _sign(secret_date, TMT_SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # 4. 拼接 Authorization
    authorization = (
        f"TC3-HMAC-SHA256 Credential={TENCENT_SECRET_ID}/{credential_scope}, "
        f"SignedHeaders=content-type;host;x-tc-action, "
        f"Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": TMT_ENDPOINT,
        "X-TC-Action": TMT_ACTION,
        "X-TC-Version": TMT_VERSION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": TMT_REGION,
    }


# ==================== 翻译核心函数 ====================


async def translate_text(
    text: str, target: str, source: str = "auto"
) -> str | None:
    """
    调用腾讯云文本翻译 API。

    :param text: 待翻译文本
    :param target: 目标语言代码 (zh / en / ja)
    :param source: 源语言代码，默认 auto 自动检测
    :return: 翻译后的文本，失败时返回 None
    """
    if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
        return None

    payload = json.dumps(
        {
            "SourceText": text,
            "Source": source,
            "Target": target,
            "ProjectId": 0,
        }
    )
    timestamp = int(time.time())
    headers = _build_auth_header(payload, timestamp)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://{TMT_ENDPOINT}", headers=headers, content=payload
        )

    if resp.status_code != 200:
        return None

    data = resp.json()
    response = data.get("Response", {})
    if "Error" in response:
        return None
    return response.get("TargetText")


# ==================== 注册命令 ====================
translate_cmd = on_command("翻译", priority=10, block=True)


@translate_cmd.handle()
async def handle_translate(event: MessageEvent, args: Message = CommandArg()):
    """处理 /翻译 命令"""
    if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
        await translate_cmd.finish("翻译 API 未配置，请联系管理员。")

    raw = args.extract_plain_text().strip()
    if not raw:
        supported = "、".join(LANG_DISPLAY.values())
        await translate_cmd.finish(
            f"用法：/翻译 <目标语言> <文本>\n"
            f"支持语言：{supported}\n"
            f"示例：/翻译 英文 你好世界"
        )

    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await translate_cmd.finish("请同时提供目标语言和待翻译文本，例如：/翻译 英文 你好世界")

    lang_input, text = parts
    target = LANG_MAP.get(lang_input)
    if not target:
        supported = "、".join(LANG_DISPLAY.values())
        await translate_cmd.finish(
            f"不支持的目标语言「{lang_input}」\n支持的语言：{supported}"
        )

    result = await translate_text(text, target)
    if result is None:
        await translate_cmd.finish("翻译失败，请稍后重试。")

    lang_name = LANG_DISPLAY.get(target, target)
    await translate_cmd.finish(f"【翻译 → {lang_name}】\n{result}")
