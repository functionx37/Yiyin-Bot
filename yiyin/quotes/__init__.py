"""
NoneBot2 群友语录插件
- 命令：/新增群友 <群友昵称>
- 命令：/新增别名 <已有昵称> <别名>
- 命令：/群友列表
- 命令：/上传 <群友昵称> [图片]
- 命令：/查看 <群友昵称>
- 命令：/随机群友
- 功能：记录并随机查看群友的发言截图
"""

import json
import random
import uuid
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "quotes"


# ==================== 工具函数 ====================
def _get_group_dir(group_id: str) -> Path:
    """获取群组数据目录"""
    return DATA_DIR / group_id


def _get_members_file(group_id: str) -> Path:
    """获取群组成员列表文件路径"""
    return _get_group_dir(group_id) / "members.json"


def _get_aliases_file(group_id: str) -> Path:
    """获取群组别名映射文件路径"""
    return _get_group_dir(group_id) / "aliases.json"


def _get_member_image_dir(group_id: str, member_name: str) -> Path:
    """获取群友图片存放目录"""
    return _get_group_dir(group_id) / "images" / member_name


def _load_members(group_id: str) -> list[str]:
    """加载群组成员列表"""
    members_file = _get_members_file(group_id)
    if not members_file.exists():
        return []
    with open(members_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_members(group_id: str, members: list[str]) -> None:
    """保存群组成员列表"""
    members_file = _get_members_file(group_id)
    members_file.parent.mkdir(parents=True, exist_ok=True)
    with open(members_file, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)


def _load_aliases(group_id: str) -> dict[str, str]:
    """加载别名映射 {别名: 主昵称}"""
    aliases_file = _get_aliases_file(group_id)
    if not aliases_file.exists():
        return {}
    with open(aliases_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_aliases(group_id: str, aliases: dict[str, str]) -> None:
    """保存别名映射"""
    aliases_file = _get_aliases_file(group_id)
    aliases_file.parent.mkdir(parents=True, exist_ok=True)
    with open(aliases_file, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)


def _resolve_name(group_id: str, name: str) -> str | None:
    """将输入名称解析为主昵称，支持别名查找。未找到返回 None。"""
    members = _load_members(group_id)
    if name in members:
        return name
    aliases = _load_aliases(group_id)
    canonical = aliases.get(name)
    if canonical and canonical in members:
        return canonical
    return None


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    """从命令参数和引用消息中提取图片"""
    images = [seg for seg in args if seg.type == "image"]
    if images:
        return images

    if not event.reply:
        return []

    # 从 event.reply.message 中提取
    if event.reply.message:
        reply_images = [seg for seg in event.reply.message if seg.type == "image"]
        if reply_images:
            return reply_images

    # 兜底：通过 API 获取原始消息
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


# ==================== 注册命令 ====================
add_member_cmd = on_command("新增群友", priority=10, block=True)
add_alias_cmd = on_command("新增别名", priority=10, block=True)
list_members_cmd = on_command("群友列表", priority=10, block=True)
upload_cmd = on_command("上传", priority=10, block=True)
view_cmd = on_command("查看", priority=10, block=True)
random_member_cmd = on_command("随机群友", priority=10, block=True)


# ==================== 命令处理 ====================
@add_member_cmd.handle()
async def handle_add_member(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /新增群友 命令"""
    name = args.extract_plain_text().strip()
    if not name:
        await add_member_cmd.finish("请输入群友昵称，例如：/新增群友 小明")

    group_id = str(event.group_id)
    members = _load_members(group_id)

    if name in members:
        await add_member_cmd.finish(f"群友「{name}」已存在，无需重复添加")

    aliases = _load_aliases(group_id)
    if name in aliases:
        await add_member_cmd.finish(
            f"「{name}」已被用作群友「{aliases[name]}」的别名，不能再作为主昵称"
        )

    members.append(name)
    _save_members(group_id, members)
    await add_member_cmd.finish(f"已成功添加群友「{name}」✓")


@add_alias_cmd.handle()
async def handle_add_alias(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /新增别名 命令"""
    text = args.extract_plain_text().strip()
    parts = text.split()
    if len(parts) < 2:
        await add_alias_cmd.finish(
            "请输入已有昵称和新别名，例如：/新增别名 小明 明明"
        )

    existing_name = parts[0]
    alias = parts[1]

    group_id = str(event.group_id)
    canonical = _resolve_name(group_id, existing_name)

    if not canonical:
        await add_alias_cmd.finish(
            f"群友「{existing_name}」不存在，请先使用 /新增群友 {existing_name} 添加"
        )

    members = _load_members(group_id)
    if alias in members:
        await add_alias_cmd.finish(f"「{alias}」已是一个群友的主昵称，不能用作别名")

    aliases = _load_aliases(group_id)
    if alias in aliases:
        await add_alias_cmd.finish(
            f"「{alias}」已是群友「{aliases[alias]}」的别名"
        )

    aliases[alias] = canonical
    _save_aliases(group_id, aliases)
    await add_alias_cmd.finish(f"已为群友「{canonical}」添加别名「{alias}」✓")


@list_members_cmd.handle()
async def handle_list_members(bot: Bot, event: GroupMessageEvent):
    """处理 /群友列表 命令"""
    group_id = str(event.group_id)
    members = _load_members(group_id)

    if not members:
        await list_members_cmd.finish(
            "本群还没有记录任何群友，使用 /新增群友 <昵称> 来添加吧"
        )

    aliases = _load_aliases(group_id)
    alias_map: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        alias_map.setdefault(canonical, []).append(alias)

    lines = []
    for i, name in enumerate(members):
        image_dir = _get_member_image_dir(group_id, name)
        count = len(list(image_dir.glob("*.*"))) if image_dir.exists() else 0
        alias_list = alias_map.get(name, [])
        alias_str = f"（别名：{'、'.join(alias_list)}）" if alias_list else ""
        lines.append(f"  {i + 1}. {name}{alias_str}：{count}条")
    member_list = "\n".join(lines)
    await list_members_cmd.finish(f"本群已记录的群友：\n{member_list}")


@upload_cmd.handle()
async def handle_upload(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /上传 命令"""
    name = args.extract_plain_text().strip()
    if not name:
        await upload_cmd.finish("请输入群友昵称并附带图片，例如：/上传 小明 [图片]")

    group_id = str(event.group_id)

    auto_registered = False
    canonical = _resolve_name(group_id, name)
    if not canonical:
        members = _load_members(group_id)
        members.append(name)
        _save_members(group_id, members)
        canonical = name
        auto_registered = True

    images = await _extract_images(bot, event, args)
    if not images:
        await upload_cmd.finish(
            "请在命令中附带图片或引用含图片的消息，例如：/上传 小明 [图片]"
        )

    image_dir = _get_member_image_dir(group_id, canonical)
    image_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    async with httpx.AsyncClient() as client:
        for img_seg in images:
            url = img_seg.data.get("url")
            if not url:
                continue
            try:
                resp = await client.get(url, timeout=30)
                resp.raise_for_status()
                filename = f"{uuid.uuid4().hex}.png"
                filepath = image_dir / filename
                filepath.write_bytes(resp.content)
                saved_count += 1
            except Exception:
                continue

    if saved_count == 0:
        await upload_cmd.finish("图片下载失败，请稍后重试")

    prefix = f"群友「{canonical}」已自动注册，" if auto_registered else ""
    await upload_cmd.finish(
        f"{prefix}已成功为群友「{canonical}」保存 {saved_count} 张语录截图✓"
    )


@view_cmd.handle()
async def handle_view(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /查看 命令"""
    name = args.extract_plain_text().strip()
    if not name:
        await view_cmd.finish("请输入群友昵称，例如：/查看 小明")

    group_id = str(event.group_id)
    canonical = _resolve_name(group_id, name)

    if not canonical:
        await view_cmd.finish(
            f"群友「{name}」不存在，请先使用 /新增群友 {name} 添加"
        )

    image_dir = _get_member_image_dir(group_id, canonical)
    if not image_dir.exists():
        await view_cmd.finish(
            f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
        )

    image_files = list(image_dir.glob("*.*"))
    if not image_files:
        await view_cmd.finish(
            f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
        )

    chosen = random.choice(image_files)
    image_bytes = chosen.read_bytes()

    msg = MessageSegment.text(f"群友「{canonical}」的语录：\n") + MessageSegment.image(
        image_bytes
    )
    await view_cmd.finish(msg)


@random_member_cmd.handle()
async def handle_random_member(bot: Bot, event: GroupMessageEvent):
    """处理 /随机群友 命令：从全部群友语录中随机抽取一条"""
    group_id = str(event.group_id)
    members = _load_members(group_id)

    if not members:
        await random_member_cmd.finish(
            "本群还没有记录任何群友，使用 /新增群友 <昵称> 来添加吧"
        )

    all_quotes: list[tuple[str, Path]] = []
    for name in members:
        image_dir = _get_member_image_dir(group_id, name)
        if image_dir.exists():
            for img_file in image_dir.glob("*.*"):
                all_quotes.append((name, img_file))

    if not all_quotes:
        await random_member_cmd.finish(
            "本群还没有任何语录记录，使用 /上传 <昵称> [图片] 来添加吧"
        )

    chosen_name, chosen_file = random.choice(all_quotes)
    image_bytes = chosen_file.read_bytes()

    msg = MessageSegment.text(
        f"随机抽到了群友「{chosen_name}」的语录：\n"
    ) + MessageSegment.image(image_bytes)
    await random_member_cmd.finish(msg)
