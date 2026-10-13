"""图片指令的交互层；绘制由通用资源保护在子进程中执行。"""
from __future__ import annotations
import asyncio
import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from resource_guard import render_bytes, gate
from resource_guard.renderers.salt import _normalize_salt_text

salt_cmd = on_command("盐巴", priority=10, block=True)


@salt_cmd.handle()
async def handle_salt(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = _normalize_salt_text(args.extract_plain_text())
    if not text:
        await salt_cmd.finish("用法：/盐巴 <文本>")

    try:
        gate.budget()
        out_bytes = await render_bytes('salt', {'text': text})
    except FileNotFoundError as e:
        await salt_cmd.finish(str(e))
    except Exception as e:
        await salt_cmd.finish(f"生成失败：{e}")

    await bot.send(event, MessageSegment.image(out_bytes))
    await salt_cmd.finish()
