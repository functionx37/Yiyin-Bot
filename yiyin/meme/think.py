"""图片指令的交互层；绘制由通用资源保护在子进程中执行。"""
from __future__ import annotations
import asyncio
import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from resource_guard import render_bytes, gate
from resource_guard.renderers.think import _parse_xiang_args, _load_template_map, _resolve_template

def _build_forward_nodes(bot_name: str, bot_uin: str, items: list[str]) -> list[dict]:
    return [
        {
            "type": "node",
            "data": {
                "name": bot_name,
                "uin": bot_uin,
                "content": MessageSegment.text(item),
            },
        }
        for item in items
    ]

xiang_cmd = on_command("想", priority=10, block=True)


@xiang_cmd.handle()
async def handle_xiang(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    alias, text = _parse_xiang_args(raw)

    if alias == "LIST":
        if not isinstance(event, GroupMessageEvent):
            await xiang_cmd.finish("list 仅支持群聊使用")
        templates = _load_template_map()
        ordered_names = [name for name in templates.keys() if name != "default"]
        bot_name = "37"
        bot_uin = str(bot.self_id)
        nodes = _build_forward_nodes(bot_name, bot_uin, ordered_names)
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        await xiang_cmd.finish()

    if not text:
        await xiang_cmd.finish(
            "用法：/想 <文本>\n"
            "或：/想 %<模板名> <文本>\n"
            "或：/想 list"
        )

    fallback_notice: str | None = None
    try:
        _, template = _resolve_template(alias)
    except KeyError:
        fallback_notice = f"不存在模板：{alias}"
        _, template = _resolve_template("default")
    except FileNotFoundError as e:
        await xiang_cmd.finish(str(e))

    try:
        gate.budget()
        out_bytes = await render_bytes('think', {'text': text, 'template': __import__('dataclasses').asdict(template)})
    except FileNotFoundError as e:
        await xiang_cmd.finish(str(e))
    except Exception as e:
        await xiang_cmd.finish(f"生成失败：{e}")

    if fallback_notice:
        if isinstance(event, GroupMessageEvent):
            templates = _load_template_map()
            ordered_names = [name for name in templates.keys() if name != "default"]
            bot_name = "37"
            bot_uin = str(bot.self_id)
            nodes = _build_forward_nodes(bot_name, bot_uin, ordered_names)
            await bot.send(event, fallback_notice)
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        else:
            await bot.send(event, fallback_notice)

    await bot.send(event, MessageSegment.image(out_bytes))
    await xiang_cmd.finish()
