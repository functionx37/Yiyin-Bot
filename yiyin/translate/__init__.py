"""
NoneBot2 翻译插件
- 命令：/翻译 <目标语言> [文本]
- 功能：调用 llmapi（gpt-4o）进行翻译，支持任意目标语言
- 同时对外暴露 translate_text() 供其他插件调用
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

from yiyin.llmapi import chat_completion

# ==================== 翻译核心函数 ====================


async def translate_text(text: str, target_lang: str) -> str | None:
    """
    调用 llmapi（gpt-4o）进行翻译。

    :param text: 待翻译文本
    :param target_lang: 目标语言（如 英文、日文、法语 等，支持任意语言）
    :return: 翻译后的文本，失败时返回 None
    """
    prompt = f"请帮我翻译以下文本为{target_lang}：\n\n{text}\n\n要求：只返回翻译后的文本，不要有任何解释、说明或额外内容。"
    messages = [{"role": "user", "content": prompt}]
    result = await chat_completion(
        messages,
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048,
    )
    return result.strip() if result else None


# ==================== 注册命令 ====================
translate_cmd = on_command("翻译", priority=10, block=True)


@translate_cmd.handle()
async def handle_translate(event: MessageEvent, args: Message = CommandArg()):
    """
    处理 /翻译 命令
    支持两种用法：
      1. /翻译 <目标语言> <文本>
      2. 引用一条消息并发送 /翻译 <目标语言>
    支持任意目标语言（如 英文、日文、法语、韩语 等）
    """
    raw = args.extract_plain_text().strip()
    if not raw:
        await translate_cmd.finish(
            "用法：/翻译 <目标语言> <文本>\n"
            "或引用一条消息并发送：/翻译 <目标语言>\n"
            "示例：/翻译 英文 你好世界"
        )

    parts = raw.split(maxsplit=1)
    target_lang = parts[0]
    if not target_lang:
        await translate_cmd.finish("请指定目标语言，如：/翻译 英文 你好世界")

    # 优先使用命令参数中的文本，其次从引用消息中提取
    text = parts[1] if len(parts) >= 2 else ""
    if not text and event.reply:
        text = event.reply.message.extract_plain_text().strip()
    if not text:
        await translate_cmd.finish(
            "请提供待翻译文本，或引用一条消息。\n"
            "示例：/翻译 英文 你好世界"
        )

    result = await translate_text(text, target_lang)
    if result is None:
        await translate_cmd.finish("翻译失败，请稍后重试。（请确认 YUNWU_API_KEY 已配置）")

    await translate_cmd.finish(f"【翻译 → {target_lang}】\n{result}")
