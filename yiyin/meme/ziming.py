"""图片指令的交互层；绘制由通用资源保护在子进程中执行。"""
from __future__ import annotations
import asyncio
import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from resource_guard import render_bytes, gate
from resource_guard.renderers.ziming import _parse_strong_args

qiangqiang_cmd = on_command("强强", priority=10, block=True)


@qiangqiang_cmd.handle()
async def handle_qiangqiang(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await qiangqiang_cmd.finish(
            "用法：/强强 （文本1）（文本2）（文本3）\n"
            "用中文括号括起三段文字，中间可以为空，例如：/强强 （左）（中）（右）\n"
            "支持换行：可直接在消息里换行"
        )
    parts = _parse_strong_args(raw)
    try:
        gate.budget()
        out_bytes = await render_bytes('strong', {'texts': parts})
    except FileNotFoundError as e:
        await qiangqiang_cmd.finish(str(e))
    except Exception as e:
        await qiangqiang_cmd.finish(f"生成失败：{e}")
    # 显式用 bot.send 发送图片，再 finish 结束，避免仅 finish(image) 时部分环境下不发出
    await bot.send(event, MessageSegment.image(out_bytes))
    await qiangqiang_cmd.finish()
