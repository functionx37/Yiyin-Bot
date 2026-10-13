"""图片指令的交互层；绘制由通用资源保护在子进程中执行。"""
from __future__ import annotations
import asyncio
import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from resource_guard import render_bytes, gate
from resource_guard.renderers.xnn import _first_at_qq, _AVATAR_URL

xnn_cmd = on_command("男娘", priority=10, block=True)


@xnn_cmd.handle()
async def handle_xnn(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    target = _first_at_qq(args)
    if target is None:
        target = int(event.get_user_id())

    try:
        gate.budget()
    except ValueError as error:
        await xnn_cmd.finish(str(error))

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(
                _AVATAR_URL.format(qq=target), timeout=10
            )
            resp.raise_for_status()
            avatar_bytes = resp.content
        except Exception:
            await xnn_cmd.finish("下载头像失败，请稍后重试")

    try:
        out_bytes = await render_bytes('avatar', {'avatar_bytes': avatar_bytes})
    except FileNotFoundError as e:
        await xnn_cmd.finish(str(e))
    except Exception as e:
        await xnn_cmd.finish(f"生成失败：{e}")

    await bot.send(event, MessageSegment.image(out_bytes))
    await xnn_cmd.finish()
