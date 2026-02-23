"""
NoneBot2 食物图鉴插件（按群隔离）
- 命令：/收集食物 [名字] [图片] — 保存食物图片，支持引用图片，可选名字
- 命令：/删除食物 <id> — 仅超级管理员
- 命令：/补充名字 <id> <name>
- 命令：/吃大餐 [数量] — 默认三道菜，最多十道
- 触发：有人发「吃什么」时回复「是啊，吃什么」并随机一张图请你吃
"""

import json
import random
import string
import uuid
from pathlib import Path

import httpx
from nonebot import on_command, on_keyword
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "food"


def _get_group_dir(group_id: str) -> Path:
    """获取群组数据目录（按群隔离）"""
    return DATA_DIR / group_id


def _get_index_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "index.json"


def _get_images_dir(group_id: str) -> Path:
    return _get_group_dir(group_id) / "images"


def _generate_short_id(existing_ids: set[str]) -> str:
    """生成6位字母数字组合的唯一短ID"""
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str) -> dict[str, dict]:
    """加载索引 {short_id: {"filename": str, "name": str | None}}"""
    index_file = _get_index_file(group_id)
    if not index_file.exists():
        return {}
    with open(index_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(group_id: str, index: dict[str, dict]) -> None:
    index_file = _get_index_file(group_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _add_to_index(group_id: str, filename: str, name: str | None) -> str:
    """向索引添加一条，返回 short_id"""
    index = _load_index(group_id)
    existing = set(index.keys())
    short_id = _generate_short_id(existing)
    index[short_id] = {"filename": filename, "name": name}
    _save_index(group_id, index)
    return short_id


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    """从命令参数和引用消息中提取图片"""
    images = [seg for seg in args if seg.type == "image"]
    if images:
        return images
    if not event.reply:
        return []
    if event.reply.message:
        reply_images = [seg for seg in event.reply.message if seg.type == "image"]
        if reply_images:
            return reply_images
    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw_msg = msg_data.get("message", [])
        if isinstance(raw_msg, Message):
            return [seg for seg in raw_msg if seg.type == "image"]
        if isinstance(raw_msg, str):
            parsed = Message(raw_msg)
            return [seg for seg in parsed if seg.type == "image"]
        if isinstance(raw_msg, list):
            result = []
            for seg in raw_msg:
                if isinstance(seg, MessageSegment) and seg.type == "image":
                    result.append(seg)
                elif isinstance(seg, dict) and seg.get("type") == "image":
                    result.append(MessageSegment("image", seg.get("data", {})))
            return result
    except Exception:
        pass
    return []


def _format_food_label(short_id: str, name: str | None) -> str:
    """有名字：『name』（id）；无名字：『id』"""
    if name and name.strip():
        return f"『{name.strip()}』（{short_id}）"
    return f"『{short_id}』"


# ==================== 注册命令 ====================
collect_food_cmd = on_command("收集食物", priority=10, block=True)
delete_food_cmd = on_command(
    "删除食物", priority=10, block=True, permission=SUPERUSER
)
supplement_name_cmd = on_command("补充名字", priority=10, block=True)
feast_cmd = on_command("吃大餐", priority=10, block=True)

what_to_eat_matcher = on_keyword({"吃什么"}, priority=50, block=False)


# ==================== 命令处理 ====================
@collect_food_cmd.handle()
async def handle_collect_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /收集食物 [名字] [图片]"""
    group_id = str(event.group_id)
    text = args.extract_plain_text().strip()
    # 名字参数可选，可能为空
    name = text if text else None

    images = await _extract_images(bot, event, args)
    if not images:
        await collect_food_cmd.finish(
            "请在命令中附带图片或引用含图片的消息，例如：/收集食物 蛋炒饭 [图片]"
        )

    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    saved_ids: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for img_seg in images:
            url = img_seg.data.get("url")
            if not url:
                continue
            try:
                resp = await client.get(url, timeout=30)
                resp.raise_for_status()
                filename = f"{uuid.uuid4().hex}.png"
                filepath = images_dir / filename
                filepath.write_bytes(resp.content)
                short_id = _add_to_index(group_id, filename, name)
                saved_ids.append(short_id)
            except Exception:
                logger.exception(f"下载食物图片失败: {url}")
                continue

    if not saved_ids:
        await collect_food_cmd.finish("图片下载失败，请稍后重试")

    id_str = "、".join(saved_ids)
    name_hint = f"「{name}」" if name else "（未填名字，可用 /补充名字 <id> <名字> 补充）"
    await collect_food_cmd.finish(
        f"已保存 {len(saved_ids)} 张食物图✓ {name_hint}\n食物ID：{id_str}"
    )


@delete_food_cmd.handle()
async def handle_delete_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /删除食物 <id>（仅超级管理员）"""
    food_id = args.extract_plain_text().strip()
    if not food_id:
        await delete_food_cmd.finish("请输入要删除的食物ID，例如：/删除食物 Ab3x9K")

    group_id = str(event.group_id)
    index = _load_index(group_id)
    if food_id not in index:
        await delete_food_cmd.finish(f"食物ID「{food_id}」不存在，请检查后重试")

    entry = index[food_id]
    filename = entry["filename"]
    filepath = _get_images_dir(group_id) / filename
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index)
    await delete_food_cmd.finish(f"已删除食物（ID：{food_id}）✓")


@supplement_name_cmd.handle()
async def handle_supplement_name(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /补充名字 <id> <name>"""
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await supplement_name_cmd.finish(
            "用法：/补充名字 <食物ID> <名字>，例如：/补充名字 Ab3x9K 蛋炒饭"
        )
    food_id, name = parts[0].strip(), parts[1].strip()
    if not name:
        await supplement_name_cmd.finish("请提供要补充的名字")

    group_id = str(event.group_id)
    index = _load_index(group_id)
    if food_id not in index:
        await supplement_name_cmd.finish(f"食物ID「{food_id}」不存在")

    index[food_id]["name"] = name
    _save_index(group_id, index)
    await supplement_name_cmd.finish(f"已为食物（ID：{food_id}）补充名字「{name}」✓")


@feast_cmd.handle()
async def handle_feast(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /吃大餐 [数量]：第一道菜：name（id）【图片】... 默认3道，最多10道"""
    group_id = str(event.group_id)
    text = args.extract_plain_text().strip()
    try:
        count = int(text) if text else 3
    except ValueError:
        count = 3
    count = max(1, min(10, count))

    index = _load_index(group_id)
    if not index:
        await feast_cmd.finish(
            "本群还没有收集任何食物，使用 /收集食物 [名字] [图片] 来添加吧"
        )

    ids = list(index.keys())
    if len(ids) < count:
        await feast_cmd.finish(
            f"本群目前只有 {len(ids)} 道菜，无法凑齐 {count} 道，试试 /吃大餐 {len(ids)}"
        )

    chosen_ids = random.sample(ids, count)
    images_dir = _get_images_dir(group_id)
    parts: list[MessageSegment] = []
    for i, short_id in enumerate(chosen_ids, 1):
        entry = index[short_id]
        name = entry.get("name") or None
        label = _format_food_label(short_id, name)
        ordinal = "第一道菜" if i == 1 else f"第{i}道菜"
        text_seg = MessageSegment.text(f"{ordinal}：{label}\n")
        parts.append(text_seg)
        filepath = images_dir / entry["filename"]
        if filepath.exists():
            parts.append(MessageSegment.image(filepath.read_bytes()))
    await feast_cmd.finish(Message(parts))


@what_to_eat_matcher.handle()
async def handle_what_to_eat(bot: Bot, event: GroupMessageEvent):
    """检测「吃什么」：是啊，吃什么 + 随机一张图 请你吃『name』（id）怎么样 [图片]"""
    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        return

    ids = list(index.keys())
    short_id = random.choice(ids)
    entry = index[short_id]
    name = entry.get("name") or None
    label = _format_food_label(short_id, name)
    filepath = _get_images_dir(group_id) / entry["filename"]
    if not filepath.exists():
        return

    await bot.send(event, "是啊，吃什么")
    msg = MessageSegment.text("请你吃") + MessageSegment.text(label) + MessageSegment.text("怎么样？\n") + MessageSegment.image(filepath.read_bytes())
    await what_to_eat_matcher.finish(msg)
