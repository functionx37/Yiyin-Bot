"""
NoneBot2 群刊插件
- 命令：/群刊 — 发送本群群友语录 + 食物图鉴的合并转发（聊天记录），每群每天限用一次
- 结构：第一条 群友语录 / 第二条 打包语录记录（昵称：图）/ 第三条 食物图鉴 / 第四条 打包食物记录（名字+图）
"""

import json
from datetime import date
from pathlib import Path

from nonebot import on_command
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)

# ==================== 常量与路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
QUOTES_DIR = DATA_DIR / "quotes"
FOOD_DIR = DATA_DIR / "food"
MAGAZINE_DIR = DATA_DIR / "magazine"
LAST_DATE_FILE = MAGAZINE_DIR / "last_date.json"

# 合并转发单条消息最多节点数（避免超限）
MAX_NODES_PER_FORWARD = 200


def _make_node(name: str, uin: str, content: Message) -> dict:
    return {"type": "node", "data": {"name": name, "uin": uin, "content": content}}


def _load_quotes_index(group_id: str) -> dict:
    """语录索引 { short_id: { member, filename } }"""
    index_file = QUOTES_DIR / group_id / "index.json"
    if not index_file.exists():
        return {}
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"群刊加载语录索引失败 group={group_id}: {e}")
        return {}


def _load_food_index(group_id: str) -> dict:
    """食物索引 { short_id: { filename, name? } }"""
    index_file = FOOD_DIR / group_id / "index.json"
    if not index_file.exists():
        return {}
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"群刊加载食物索引失败 group={group_id}: {e}")
        return {}


def _can_use_today(group_id: str) -> bool:
    """该群今天是否已使用过群刊"""
    MAGAZINE_DIR.mkdir(parents=True, exist_ok=True)
    if not LAST_DATE_FILE.exists():
        return True
    try:
        with open(LAST_DATE_FILE, "r", encoding="utf-8") as f:
            last = json.load(f)
        return last.get(group_id) != date.today().isoformat()
    except Exception:
        return True


def _mark_used_today(group_id: str) -> None:
    """记录该群今日已使用"""
    MAGAZINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LAST_DATE_FILE.exists():
            with open(LAST_DATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        data[group_id] = date.today().isoformat()
        with open(LAST_DATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"群刊记录使用日期失败 group={group_id}: {e}")


def _build_quote_nodes(group_id: str, bot_name: str, bot_uin: str) -> list[dict]:
    """构建语录段节点列表（昵称：+ 图片）"""
    index = _load_quotes_index(group_id)
    images_dir = QUOTES_DIR / group_id / "images"
    nodes = []
    for short_id, entry in index.items():
        member = entry.get("member") or short_id
        filename = entry.get("filename")
        if not filename:
            continue
        filepath = images_dir / member / filename
        if not filepath.exists():
            continue
        try:
            img_bytes = filepath.read_bytes()
        except Exception as e:
            logger.debug(f"群刊读取语录图片失败 {filepath}: {e}")
            continue
        content = Message(MessageSegment.text(f"{member}：")) + MessageSegment.image(img_bytes)
        nodes.append(_make_node(bot_name, bot_uin, content))
    return nodes


def _build_food_nodes(group_id: str, bot_name: str, bot_uin: str) -> list[dict]:
    """构建食物段节点列表（名字或 id + 图片）"""
    index = _load_food_index(group_id)
    images_dir = FOOD_DIR / group_id / "images"
    nodes = []
    for short_id, entry in index.items():
        name = entry.get("name")
        display = (name.strip() if name and name.strip() else short_id)
        filename = entry.get("filename")
        if not filename:
            continue
        filepath = images_dir / filename
        if not filepath.exists():
            continue
        try:
            img_bytes = filepath.read_bytes()
        except Exception as e:
            logger.debug(f"群刊读取食物图片失败 {filepath}: {e}")
            continue
        content = Message(MessageSegment.text(display + "\n")) + MessageSegment.image(img_bytes)
        nodes.append(_make_node(bot_name, bot_uin, content))
    return nodes


# ==================== 注册命令 ====================
magazine_cmd = on_command("群刊", priority=10, block=True)


@magazine_cmd.handle()
async def handle_magazine(bot: Bot, event: GroupMessageEvent):
    """处理 /群刊：每群每天一次，发送语录+食物图鉴的合并转发"""
    group_id = str(event.group_id)

    if not _can_use_today(group_id):
        await magazine_cmd.finish("本群今日已生成过群刊，明天再来吧~")

    await magazine_cmd.send("群刊生成中…")

    try:
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "一印Bot")
        bot_uin = str(bot.self_id)
    except Exception as e:
        logger.warning(f"群刊获取 Bot 信息失败: {e}")
        bot_name = "一印Bot"
        bot_uin = str(bot.self_id)

    quote_nodes = _build_quote_nodes(group_id, bot_name, bot_uin)
    food_nodes = _build_food_nodes(group_id, bot_name, bot_uin)

    if not quote_nodes and not food_nodes:
        await bot.send(event, "本群暂无语录与食物记录，无法生成群刊。")
        return

    title_quotes = _make_node(
        bot_name, bot_uin, Message(MessageSegment.text("群友语录"))
    )
    title_food = _make_node(
        bot_name, bot_uin, Message(MessageSegment.text("食物图鉴"))
    )

    def chunk(nodes: list[dict], max_per: int) -> list[list[dict]]:
        if max_per <= 0:
            return [nodes] if nodes else []
        return [nodes[i : i + max_per] for i in range(0, len(nodes), max_per)]

    # 顺序：第一条 群友语录 / 第二条 语录聊天记录(可多段) / 第三条 食物图鉴 / 第四条 食物聊天记录(可多段)
    all_forward_messages: list[list[dict]] = []

    all_forward_messages.append([title_quotes])
    if quote_nodes:
        for c in chunk(quote_nodes, MAX_NODES_PER_FORWARD):
            all_forward_messages.append(c)
    all_forward_messages.append([title_food])
    if food_nodes:
        for c in chunk(food_nodes, MAX_NODES_PER_FORWARD):
            all_forward_messages.append(c)

    sent = 0
    try:
        for nodes in all_forward_messages:
            if not nodes:
                continue
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
            sent += 1
        if sent > 0:
            _mark_used_today(group_id)
    except Exception as e:
        logger.exception(f"群刊发送合并转发失败: {e}")
        if sent == 0:
            await bot.send(event, "群刊生成失败，请稍后重试。")
        else:
            _mark_used_today(group_id)
            await bot.send(event, "群刊已部分发送，后续段发送失败，请稍后重试。")
        return

    if sent == 0:
        await bot.send(event, "群刊生成失败，请稍后重试。")
