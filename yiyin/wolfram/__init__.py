"""
NoneBot2 WolframAlpha 数学问题求解插件
- 命令：/算 <问题描述>
- 功能：调用 WolframAlpha Full Results API，以合并转发消息返回解答
"""

import os

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    GroupMessageEvent,
    MessageSegment,
    Message,
)
from nonebot.params import CommandArg

# ==================== 配置 ====================
WOLFRAM_APPID: str = os.environ.get("WOLFRAM_APPID", "")
WOLFRAM_API = "https://api.wolframalpha.com/v2/query"

# ==================== 注册命令 ====================
wolfram_cmd = on_command("算", priority=10, block=True)


@wolfram_cmd.handle()
async def handle_wolfram(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """处理 /算 命令"""

    # 1. 检查 API 配置
    if not WOLFRAM_APPID:
        await wolfram_cmd.finish("WolframAlpha API 未配置，请联系管理员。")

    # 2. 获取用户输入的问题
    query = args.extract_plain_text().strip()
    if not query:
        await wolfram_cmd.finish("请输入要计算的问题，例如：/算 integrate x^2 dx")

    user_id = event.get_user_id()

    # 3. 调用 WolframAlpha Full Results API
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                WOLFRAM_API,
                params={
                    "appid": WOLFRAM_APPID,
                    "input": query,
                    "output": "json",
                    "units": "metric",
                },
            )

        if resp.status_code != 200:
            await wolfram_cmd.finish(
                MessageSegment.at(user_id) + " 查询失败，请稍后重试。"
            )

        data = resp.json()
        result = data.get("queryresult", {})

        if not result.get("success"):
            # 尝试从 didyoumeans 提供建议
            tips = result.get("tips", {}).get("text", "")
            hint = f"\n提示：{tips}" if tips else ""
            await wolfram_cmd.finish(
                MessageSegment.at(user_id)
                + f" WolframAlpha 无法理解该问题，请尝试换一种表述。{hint}"
            )

        pods = result.get("pods", [])
        if not pods:
            await wolfram_cmd.finish(
                MessageSegment.at(user_id) + " 未获取到结果，请尝试换一种表述。"
            )

        # 4. 构建合并转发消息节点
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "WolframAlpha")
        bot_uin = str(bot.self_id)

        nodes = []
        for pod in pods:
            title = pod.get("title", "未知")
            subpods = pod.get("subpods", [])

            msg = Message(f"【{title}】\n")

            for subpod in subpods:
                img = subpod.get("img", {})
                img_src = img.get("src", "")
                plaintext = subpod.get("plaintext", "")

                if img_src:
                    msg += MessageSegment.image(img_src)
                if plaintext:
                    msg += f"\n{plaintext}"

            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": bot_name,
                        "uin": bot_uin,
                        "content": msg,
                    },
                }
            )

        # 5. 发送合并转发消息
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(
                group_id=event.group_id, messages=nodes
            )
        else:
            await bot.send_private_forward_msg(
                user_id=int(user_id), messages=nodes
            )

    except httpx.TimeoutException:
        await wolfram_cmd.finish(
            MessageSegment.at(user_id) + " 查询超时，请稍后重试。"
        )
    except Exception:
        await wolfram_cmd.finish(
            MessageSegment.at(user_id) + " 查询出错，请稍后重试。"
        )
