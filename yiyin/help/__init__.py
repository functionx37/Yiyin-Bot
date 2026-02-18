"""
NoneBot2 帮助菜单插件
- 命令：@Bot /help  — 读取 help.json，以合并转发消息（聊天记录）形式发送帮助菜单
"""

import json
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.rule import to_me

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HELP_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "help.json"

# ==================== 加载帮助数据 ====================
with open(HELP_JSON_PATH, "r", encoding="utf-8") as f:
    HELP_DATA: list[dict] = json.load(f)

# ==================== 注册命令 ====================
help_cmd = on_command("help", rule=to_me(), priority=10, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: GroupMessageEvent):
    """处理 @Bot /help 命令：以合并转发消息形式发送帮助菜单"""
    bot_info = await bot.get_login_info()
    bot_name = bot_info.get("nickname", "一印Bot")
    bot_uin = str(bot.self_id)

    nodes = []
    for module in HELP_DATA:
        module_name = module.get("module", "未知模块")
        functions = module.get("function", [])

        lines = [f"📦 {module_name}"]
        for func in functions:
            cmd = func.get("command", "")
            desc = func.get("description", "")
            lines.append(f"  {cmd}\n    {desc}")

        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": bot_uin,
                    "content": Message(MessageSegment.text("\n".join(lines))),
                },
            }
        )

    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
