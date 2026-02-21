"""
NoneBot2 群友语录插件
- 命令：/新增群友 <群友昵称>
- 命令：/新增别名 <已有昵称> <别名>
- 命令：/群友列表
- 命令：/上传 <群友昵称> [图片]
- 命令：/截图上传 <群友昵称> [引用消息]
- 命令：/查看 <群友昵称>
- 命令：/随机群友
- 命令：/删除语录 <ID>（仅超级管理员）
- 功能：记录并随机查看群友的发言截图
"""

import json
import random
import string
import uuid
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

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


def _get_index_file(group_id: str) -> Path:
    """获取群组语录索引文件路径"""
    return _get_group_dir(group_id) / "index.json"


def _generate_short_id(existing_ids: set[str]) -> str:
    """生成6位字母数字组合的唯一短ID"""
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _load_index(group_id: str) -> dict[str, dict]:
    """加载语录索引 {short_id: {"member": str, "filename": str}}，
    首次加载时自动为已有图片生成索引。"""
    index_file = _get_index_file(group_id)
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    indexed_files: set[str] = set()
    for entry in index.values():
        indexed_files.add(f"{entry['member']}/{entry['filename']}")

    images_dir = _get_group_dir(group_id) / "images"
    changed = False
    if images_dir.exists():
        existing_ids = set(index.keys())
        for member_dir in images_dir.iterdir():
            if not member_dir.is_dir():
                continue
            member_name = member_dir.name
            for img_file in member_dir.glob("*.*"):
                key = f"{member_name}/{img_file.name}"
                if key not in indexed_files:
                    short_id = _generate_short_id(existing_ids)
                    existing_ids.add(short_id)
                    index[short_id] = {
                        "member": member_name,
                        "filename": img_file.name,
                    }
                    changed = True

    if changed:
        _save_index(group_id, index)
    return index


def _save_index(group_id: str, index: dict[str, dict]) -> None:
    """保存语录索引"""
    index_file = _get_index_file(group_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _add_to_index(group_id: str, member: str, filename: str) -> str:
    """向索引中添加一条记录，返回生成的短ID"""
    index = _load_index(group_id)
    existing_ids = set(index.keys())
    short_id = _generate_short_id(existing_ids)
    index[short_id] = {"member": member, "filename": filename}
    _save_index(group_id, index)
    return short_id


def _find_id_by_filepath(group_id: str, member: str, filename: str) -> str | None:
    """通过成员名和文件名查找短ID"""
    index = _load_index(group_id)
    for short_id, entry in index.items():
        if entry["member"] == member and entry["filename"] == filename:
            return short_id
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
screenshot_upload_cmd = on_command("截图上传", priority=10, block=True)
view_cmd = on_command("查看", priority=10, block=True)
random_member_cmd = on_command("随机群友", priority=10, block=True)
delete_quote_cmd = on_command(
    "删除语录", priority=10, block=True, permission=SUPERUSER
)


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
        alias_str = f"（{'、'.join(alias_list)}）" if alias_list else ""
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

    saved_ids: list[str] = []
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
                short_id = _add_to_index(group_id, canonical, filename)
                saved_ids.append(short_id)
            except Exception:
                continue

    if not saved_ids:
        await upload_cmd.finish("图片下载失败，请稍后重试")

    prefix = f"群友「{canonical}」已自动注册，" if auto_registered else ""
    id_str = "、".join(saved_ids)
    await upload_cmd.finish(
        f"{prefix}已成功为群友「{canonical}」保存 {len(saved_ids)} 张语录截图✓\n"
        f"语录ID：{id_str}"
    )


async def _extract_reply_text(bot: Bot, event: GroupMessageEvent) -> str:
    """从引用消息中提取文字内容"""
    if not event.reply:
        return ""

    if event.reply.message:
        parts: list[str] = []
        for seg in event.reply.message:
            if seg.type == "text":
                parts.append(seg.data.get("text", ""))
            elif seg.type == "at":
                qq = seg.data.get("qq", "")
                try:
                    info = await bot.get_group_member_info(
                        group_id=event.group_id, user_id=int(qq)
                    )
                    parts.append(
                        f"@{info.get('card') or info.get('nickname') or qq}"
                    )
                except Exception:
                    parts.append(f"@{qq}")
        result = "".join(parts).strip()
        if result:
            return result

    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw = msg_data.get("message", [])
        if isinstance(raw, str):
            return Message(raw).extract_plain_text()
        if isinstance(raw, list):
            return "".join(
                s.get("data", {}).get("text", "")
                for s in raw
                if isinstance(s, dict) and s.get("type") == "text"
            ).strip()
    except Exception:
        pass
    return ""


@screenshot_upload_cmd.handle()
async def handle_screenshot_upload(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /截图上传 命令：引用消息生成聊天截图并保存为语录"""
    from .draw import async_generate_chat_screenshot

    name = args.extract_plain_text().strip()
    if not name:
        await screenshot_upload_cmd.finish(
            "请输入群友昵称并引用一条消息，例如：/截图上传 小明（引用消息）"
        )

    if not event.reply:
        await screenshot_upload_cmd.finish(
            "请引用一条消息来生成截图，例如回复某条消息并输入：/截图上传 小明"
        )

    reply_text = await _extract_reply_text(bot, event)
    if not reply_text:
        await screenshot_upload_cmd.finish("引用的消息没有文字内容，无法生成截图")

    sender_id = event.reply.sender.user_id
    group_id = str(event.group_id)

    try:
        member_info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=sender_id
        )
        sender_nick = (
            member_info.get("card") or member_info.get("nickname") or "群友"
        )
    except Exception:
        sender_nick = getattr(event.reply.sender, "nickname", None) or "群友"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"http://q1.qlogo.cn/g?b=qq&nk={sender_id}&s=100", timeout=10
            )
            resp.raise_for_status()
            avatar_bytes = resp.content
        except Exception:
            avatar_bytes = b""

    screenshot_bytes = await async_generate_chat_screenshot(
        avatar_bytes, sender_nick, reply_text
    )

    auto_registered = False
    canonical = _resolve_name(group_id, name)
    if not canonical:
        members = _load_members(group_id)
        members.append(name)
        _save_members(group_id, members)
        canonical = name
        auto_registered = True

    image_dir = _get_member_image_dir(group_id, canonical)
    image_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    filepath = image_dir / filename
    filepath.write_bytes(screenshot_bytes)
    short_id = _add_to_index(group_id, canonical, filename)

    prefix = f"群友「{canonical}」已自动注册，" if auto_registered else ""
    msg = MessageSegment.text(
        f"{prefix}已为群友「{canonical}」生成并保存截图✓\n语录ID：{short_id}\n"
    ) + MessageSegment.image(screenshot_bytes)
    await screenshot_upload_cmd.finish(msg)


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

    short_id = _find_id_by_filepath(group_id, canonical, chosen.name)
    id_hint = f"（ID：{short_id}）" if short_id else ""
    msg = MessageSegment.text(
        f"群友「{canonical}」的语录{id_hint}：\n"
    ) + MessageSegment.image(image_bytes)
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

    short_id = _find_id_by_filepath(group_id, chosen_name, chosen_file.name)
    id_hint = f"（ID：{short_id}）" if short_id else ""
    msg = MessageSegment.text(
        f"随机抽到了群友「{chosen_name}」的语录{id_hint}：\n"
    ) + MessageSegment.image(image_bytes)
    await random_member_cmd.finish(msg)


@delete_quote_cmd.handle()
async def handle_delete_quote(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /删除语录 命令（仅超级管理员）"""
    quote_id = args.extract_plain_text().strip()
    if not quote_id:
        await delete_quote_cmd.finish("请输入要删除的语录ID，例如：/删除语录 Ab3x9K")

    group_id = str(event.group_id)
    index = _load_index(group_id)

    if quote_id not in index:
        await delete_quote_cmd.finish(f"语录ID「{quote_id}」不存在，请检查后重试")

    entry = index[quote_id]
    member = entry["member"]
    filename = entry["filename"]

    filepath = _get_member_image_dir(group_id, member) / filename
    if filepath.exists():
        filepath.unlink()

    del index[quote_id]
    _save_index(group_id, index)

    await delete_quote_cmd.finish(
        f"已删除群友「{member}」的语录（ID：{quote_id}）✓"
    )
