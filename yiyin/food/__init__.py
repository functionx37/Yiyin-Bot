"""
NoneBot2 食物图鉴插件（按群隔离）
- 命令：/收集食物 [名字] [图片] — 保存食物图片，支持引用图片，可选名字
- 命令：/删除食物 <id> — 仅超级管理员
- 命令：/补充名字 <id> <name>
- 命令：/隐藏 <id> — 将普通食物设为隐藏食物
- 命令：/吃大餐 [数量] — 默认三道菜，最多十道
- 触发：有人发「吃什么」时回复「是啊，吃什么」并随机一张图请你吃（单抽有概率触发隐藏食物）
"""

import asyncio
import json
import random
import string
from pathlib import Path

import httpx
from nonebot import on_command, on_keyword
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

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


def _get_hidden_prob_file(group_id: str) -> Path:
    return _get_group_dir(group_id) / "hidden_prob.json"


def _load_hidden_prob(group_id: str) -> int:
    """加载隐藏食物触发概率（1-100），默认 3"""
    f = _get_hidden_prob_file(group_id)
    if not f.exists():
        return 3
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return max(1, min(100, int(data.get("prob", 3))))
    except Exception:
        return 3


def _save_hidden_prob(group_id: str, prob: int) -> None:
    f = _get_hidden_prob_file(group_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump({"prob": prob}, fp, ensure_ascii=False)


def _generate_short_id(existing_ids: set[str]) -> str:
    """生成6位字母数字组合的唯一短ID"""
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str) -> dict[str, dict]:
    """加载索引 {short_id: {"name": str | None}}，图片文件名为 {short_id}.png"""
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


def _add_to_index(group_id: str, name: str | None) -> str:
    """向索引添加一条，返回 short_id。图片需保存为 images/{short_id}.png"""
    index = _load_index(group_id)
    existing = set(index.keys())
    short_id = _generate_short_id(existing)
    index[short_id] = {"name": name}
    _save_index(group_id, index)
    return short_id


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    """从命令参数和引用消息中提取图片，支持多张图片（含引用）依次处理"""
    images: list[MessageSegment] = []
    for seg in args:
        if seg.type == "image":
            images.append(seg)
    if event.reply:
        reply_images: list[MessageSegment] = []
        if event.reply.message:
            reply_images = [seg for seg in event.reply.message if seg.type == "image"]
        if not reply_images:
            try:
                msg_data = await bot.get_msg(message_id=event.reply.message_id)
                raw_msg = msg_data.get("message", [])
                if isinstance(raw_msg, Message):
                    reply_images = [seg for seg in raw_msg if seg.type == "image"]
                elif isinstance(raw_msg, str):
                    parsed = Message(raw_msg)
                    reply_images = [seg for seg in parsed if seg.type == "image"]
                elif isinstance(raw_msg, list):
                    for seg in raw_msg:
                        if isinstance(seg, MessageSegment) and seg.type == "image":
                            reply_images.append(seg)
                        elif isinstance(seg, dict) and seg.get("type") == "image":
                            reply_images.append(
                                MessageSegment("image", seg.get("data", {}))
                            )
            except Exception:
                pass
        images.extend(reply_images)
    return images


def delete_food(group_id: str, food_id: str) -> bool:
    """删除指定食物记录，供其他插件调用。成功返回 True，不存在返回 False。"""
    index = _load_index(group_id)
    if food_id not in index:
        return False
    entry = index[food_id]
    fn = entry.get("filename") or f"{food_id}.png"
    filepath = _get_images_dir(group_id) / fn
    if filepath.exists():
        filepath.unlink()
    del index[food_id]
    _save_index(group_id, index)
    return True


def _format_food_label(short_id: str, name: str | None) -> str:
    """有名字：『name』（id）；无名字：『id』"""
    if name and name.strip():
        return f"『{name.strip()}』（{short_id}）"
    return f"『{short_id}』"


async def add_food_from_image_url(
    group_id: str, image_url: str, name: str | None
) -> str | None:
    """从图片 URL 保存食物到图鉴，供其他插件调用。成功返回提示消息，失败返回 None。"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            content = resp.content
    except Exception:
        logger.exception(f"下载食物图片失败: {image_url}")
        return None

    index = _load_index(group_id)
    existing_ids = set(index.keys())
    short_id = _generate_short_id(existing_ids)
    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        filepath = images_dir / f"{short_id}.png"
        filepath.write_bytes(content)
        index[short_id] = {"name": name}
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存食物文件失败")
        filepath.unlink(missing_ok=True)
        return None

    name_hint = f"「{name}」" if name else "（未填名字，可用 /补充名字 <id> <名字> 补充）"
    return f"已保存 1 张食物图✓ {name_hint}\n食物ID：{short_id}"


# ==================== 注册命令 ====================
collect_food_cmd = on_command("收集食物", priority=10, block=True)
delete_food_cmd = on_command(
    "删除食物", priority=10, block=True, permission=SUPERUSER
)
supplement_name_cmd = on_command("补充名字", priority=10, block=True)
hidden_food_cmd = on_command(
    "隐藏", priority=10, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER
)
feast_cmd = on_command("吃大餐", priority=10, block=True)

what_to_eat_matcher = on_keyword({"吃什么"}, priority=50, block=False)


# ==================== 命令处理 ====================
@collect_food_cmd.handle()
async def handle_collect_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /收集食物 [名字] [图片]：全部下载成功才写入，任一出错则回滚"""
    group_id = str(event.group_id)
    text = args.extract_plain_text().strip()
    name = text if text else None

    images = await _extract_images(bot, event, args)
    if not images:
        await collect_food_cmd.finish(
            "请在命令中附带图片或引用含图片的消息，例如：/收集食物 蛋炒饭 [图片]"
        )

    # 先下载全部图片，任一出错则不改动 index.json
    index = _load_index(group_id)
    existing_ids = set(index.keys())
    downloaded: list[tuple[str, bytes]] = []  # (short_id, content)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for img_seg in images:
            url = img_seg.data.get("url")
            if not url:
                continue
            try:
                resp = await client.get(url, timeout=30)
                resp.raise_for_status()
                short_id = _generate_short_id(existing_ids)
                existing_ids.add(short_id)
                downloaded.append((short_id, resp.content))
            except Exception:
                logger.exception(f"下载食物图片失败: {url}")
                await collect_food_cmd.finish(
                    "图片下载失败，已回滚（未修改数据），请稍后重试"
                )

    if not downloaded:
        await collect_food_cmd.finish("未获取到有效图片，请检查后重试")

    # 全部下载成功，再写入 index 和文件（任一步失败则回滚）
    images_dir = _get_images_dir(group_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    try:
        for short_id, content in downloaded:
            index[short_id] = {"name": name}
            filepath = images_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存食物文件失败，回滚")
        for short_id, _ in downloaded:
            (images_dir / f"{short_id}.png").unlink(missing_ok=True)
        await collect_food_cmd.finish("保存失败，已回滚（未修改数据），请稍后重试")

    id_str = "、".join(sid for sid, _ in downloaded)
    name_hint = f"「{name}」" if name else "（未填名字，可用 /补充名字 <id> <名字> 补充）"
    await collect_food_cmd.finish(
        f"已保存 {len(downloaded)} 张食物图✓ {name_hint}\n食物ID：{id_str}"
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
    fn = entry.get("filename") or f"{food_id}.png"
    filepath = _get_images_dir(group_id) / fn
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


@hidden_food_cmd.handle()
async def handle_hidden_food(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /隐藏 <id>：将普通食物设为隐藏食物"""
    food_id = args.extract_plain_text().strip()
    if not food_id:
        await hidden_food_cmd.finish("用法：/隐藏 <食物ID>，例如：/隐藏 Ab3x9K")

    group_id = str(event.group_id)
    index = _load_index(group_id)
    if food_id not in index:
        await hidden_food_cmd.finish(f"食物ID「{food_id}」不存在")

    index[food_id]["hidden"] = True
    _save_index(group_id, index)
    await hidden_food_cmd.finish(f"已将食物（ID：{food_id}）设为隐藏食物✓")


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

    ids = [sid for sid, e in index.items() if not e.get("hidden")]
    if not ids:
        await feast_cmd.finish(
            "本群没有普通食物，吃大餐仅从普通食物中抽取（隐藏食物仅能从「吃什么」单抽获得）"
        )
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
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = images_dir / fn
        if filepath.exists():
            parts.append(MessageSegment.image(filepath.read_bytes()))
    await feast_cmd.finish(Message(parts))


@what_to_eat_matcher.handle()
async def handle_what_to_eat(bot: Bot, event: GroupMessageEvent):
    """检测「吃什么」：是啊，吃什么 + 随机一张图 请你吃『name』（id）怎么样 [图片]；单抽有概率触发隐藏食物"""
    group_id = str(event.group_id)
    index = _load_index(group_id)
    if not index:
        return

    # 分离普通食物与隐藏食物
    normal_ids = [sid for sid, e in index.items() if not e.get("hidden")]
    hidden_ids = [sid for sid, e in index.items() if e.get("hidden")]

    # 若有隐藏食物，按概率判定是否触发
    triggered_hidden = False
    if hidden_ids:
        prob = _load_hidden_prob(group_id)
        if random.randint(1, 100) <= prob:
            triggered_hidden = True
            _save_hidden_prob(group_id, 3)  # 抽中后概率重置为 3%
        else:
            _save_hidden_prob(group_id, min(100, prob + 1))  # 未中则 +1%

    if triggered_hidden and hidden_ids:
        # 从隐藏食物中抽取
        short_id = random.choice(hidden_ids)
        entry = index[short_id]
        name = entry.get("name") or short_id
        food_name = name.strip() if name and name.strip() else short_id
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = _get_images_dir(group_id) / fn
        if not filepath.exists():
            return

        await bot.send(event, "是啊，吃什么")
        user_id = event.get_user_id()
        text_msg = MessageSegment.text("恭喜") + MessageSegment.at(user_id) + MessageSegment.text(f"，请您享用{food_name}：")
        await bot.send(event, text_msg)
        img_resp = await bot.send(event, MessageSegment.image(filepath.read_bytes()))

        async def _recall_image():
            await asyncio.sleep(10)
            try:
                msg_id = None
                if isinstance(img_resp, dict):
                    msg_id = img_resp.get("message_id") or (img_resp.get("data") or {}).get("message_id")
                if msg_id is not None:
                    await bot.call_api("delete_msg", message_id=msg_id)
            except Exception as e:
                logger.warning(f"撤回隐藏食物图片失败: {e}")

        asyncio.create_task(_recall_image())
        await what_to_eat_matcher.finish()
        return

    # 普通抽取：从普通食物中选（若无普通食物则从全部中选）
    ids = normal_ids if normal_ids else list(index.keys())
    short_id = random.choice(ids)
    entry = index[short_id]
    name = entry.get("name") or None
    label = _format_food_label(short_id, name)
    fn = entry.get("filename") or f"{short_id}.png"
    filepath = _get_images_dir(group_id) / fn
    if not filepath.exists():
        return

    await bot.send(event, "是啊，吃什么")
    msg = MessageSegment.text("请你吃") + MessageSegment.text(label) + MessageSegment.text("怎么样？\n") + MessageSegment.image(filepath.read_bytes())
    await what_to_eat_matcher.finish(msg)
