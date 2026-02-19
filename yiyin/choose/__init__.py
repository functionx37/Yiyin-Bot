"""
NoneBot2 随机选择插件
- 命令：/选 <选项1>还是<选项2>[还是<选项3>...]
- 功能：从给定的多个选项中随机选择一个
"""

import random

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

choose_cmd = on_command("选", priority=10, block=True)


@choose_cmd.handle()
async def handle_choose(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await choose_cmd.finish(
            "用法：/选 <选项1>还是<选项2>[还是<选项3>...]\n"
            "示例：/选 火锅还是烧烤还是麻辣烫"
        )

    options = [opt.strip() for opt in raw.split("还是") if opt.strip()]

    if len(options) < 2:
        await choose_cmd.finish(
            "至少需要两个选项哦，用「还是」分隔\n"
            "示例：/选 火锅还是烧烤"
        )

    chosen = random.choice(options)
    await choose_cmd.finish(f"我建议你选择：{chosen}")
