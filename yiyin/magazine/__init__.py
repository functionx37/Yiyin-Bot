"""
NoneBot2 群刊插件
- 命令：/群刊 — 发送本群群友语录 + 食物图鉴的合并转发（聊天记录），每群每天限用一次
- 所有内容合并为一条 [聊天记录]，内部用标题节点分隔语录和食物段
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


def _make_node(name: str, uin: str, content: Message) -> dict:
    return {"type": "node", "data": {"name": name, "uin": uin, "content": content}}


def _load_quotes_index(group_id: str) -> dict:
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
        content = Message(MessageSegment.text(f"『{member}』：")) + MessageSegment.image(img_bytes)
        nodes.append(_make_node(bot_name, bot_uin, content))
    return nodes


def _build_food_nodes(group_id: str, bot_name: str, bot_uin: str) -> list[dict]:
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
        content = Message(MessageSegment.text(f"『{display}』：\n")) + MessageSegment.image(img_bytes)
        nodes.append(_make_node(bot_name, bot_uin, content))
    return nodes


# ==================== 注册命令 ====================
magazine_cmd = on_command("群刊", priority=10, block=True)


@magazine_cmd.handle()
async def handle_magazine(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)

    if not _can_use_today(group_id):
        await magazine_cmd.finish("本群今日已生成过群刊，明天再来吧~")

    await magazine_cmd.send("群刊生成中…")

    try:
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "YiyinBot")
        bot_uin = str(bot.self_id)
    except Exception as e:
        logger.warning(f"群刊获取 Bot 信息失败: {e}")
        bot_name = "YiyinBot"
        bot_uin = str(bot.self_id)

    quote_nodes = _build_quote_nodes(group_id, bot_name, bot_uin)
    food_nodes = _build_food_nodes(group_id, bot_name, bot_uin)

    if not quote_nodes and not food_nodes:
        await bot.send(event, "本群暂无语录与食物记录，无法生成群刊。")
        return

    # 所有内容合并到一个 [聊天记录] 里，用标题节点分隔各段
    nodes: list[dict] = []
    if quote_nodes:
        nodes.append(
            _make_node(bot_name, bot_uin, Message(MessageSegment.text("📖 群友语录")))
        )
        nodes.extend(quote_nodes)
    if food_nodes:
        nodes.append(
            _make_node(bot_name, bot_uin, Message(MessageSegment.text("🍽️ 食物图鉴")))
        )
        nodes.extend(food_nodes)

    try:
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        _mark_used_today(group_id)
    except Exception as e:
        logger.warning(f"群刊合并转发失败: {e}")
        await bot.send(event, "群刊生成失败，请稍后重试。")
