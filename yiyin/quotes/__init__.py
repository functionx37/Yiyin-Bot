"""
NoneBot2 群友语录插件
- 命令：/新增群友 <群友昵称>
- 命令：/新增别名 <已有昵称> <别名>（若两者均存在，则把后者当别名并合并其语录到前者）
- 命令：/群友列表
- 命令：/上传 <群友昵称> [图片]（支持多张图片含引用，依次处理；出错回滚）
- 命令：/截图上传 <群友昵称> [引用消息]
- 命令：/查看 <群友昵称>（以合并转发发送该群友全部语录，格式「ID」【对应截图】）
- 命令：/随机群友 [昵称]（等概率随机一个有语录的群友再随机一条；指定昵称则从该群友语录中随机）
- 命令：/随机语录（从本群全部语录中随机抽取一条）
- 命令：/随机精华（从群精华消息中随机抽一条，格式：昵称：内容）
- 命令：/删除语录 <ID>（仅超级管理员，支持引用语录消息后 /删除语录 自动提取 ID）
- 命令：/删除群友 <昵称>（仅超级管理员，打上删除标记，不再显示但不清理 data）
- 功能：记录并随机查看群友的发言截图
"""

import json
import random
import re
import string
from pathlib import Path

import httpx
from nonebot import on_command, on_message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from yiyin.utils import image_segment_from_path

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


def _get_deleted_members_file(group_id: str) -> Path:
    """获取已删除群友列表文件路径"""
    return _get_group_dir(group_id) / "deleted_members.json"


def _load_deleted_members(group_id: str) -> set[str]:
    """加载已删除群友集合（打上删除标记后不再显示，但不清理 data）"""
    f = _get_deleted_members_file(group_id)
    if not f.exists():
        return set()
    with open(f, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    return set(data) if isinstance(data, list) else set()


def _save_deleted_members(group_id: str, deleted: set[str]) -> None:
    """保存已删除群友列表"""
    f = _get_deleted_members_file(group_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(sorted(deleted), fp, ensure_ascii=False, indent=2)


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


def _resolve_name(group_id: str, name: str, exclude_deleted: bool = False) -> str | None:
    """将输入名称解析为主昵称，支持别名查找。未找到返回 None。
    exclude_deleted=True 时，若解析结果为已删除群友则返回 None。"""
    members = _load_members(group_id)
    if name in members:
        canonical = name
    else:
        aliases = _load_aliases(group_id)
        canonical = aliases.get(name)
        if not canonical or canonical not in members:
            return None
    if exclude_deleted:
        deleted = _load_deleted_members(group_id)
        if canonical in deleted:
            return None
    return canonical


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
    """加载语录索引 {short_id: {"member": str}}，图片文件名为 {short_id}.png。
    首次加载时自动为已有图片（旧格式或未索引）生成索引并规范化命名。"""
    index_file = _get_index_file(group_id)
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    indexed_keys: set[str] = set()
    for short_id, entry in index.items():
        member = entry.get("member")
        if not member:
            continue
        indexed_keys.add(f"{member}/{short_id}")  # 新格式: 文件为 short_id.png
        fn = entry.get("filename")
        if fn:
            indexed_keys.add(f"{member}/{fn}")  # 旧格式兼容

    images_dir = _get_group_dir(group_id) / "images"
    changed = False
    if images_dir.exists():
        existing_ids = set(index.keys())
        for member_dir in images_dir.iterdir():
            if not member_dir.is_dir():
                continue
            member_name = member_dir.name
            for img_file in member_dir.glob("*.*"):
                if img_file.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    continue
                key_name = f"{member_name}/{img_file.name}"
                key_stem = f"{member_name}/{img_file.stem}"
                if key_name in indexed_keys or key_stem in indexed_keys:
                    continue
                if img_file.stem in existing_ids:
                    continue
                    short_id = _generate_short_id(existing_ids)
                    existing_ids.add(short_id)
                    new_path = member_dir / f"{short_id}.png"
                    if img_file != new_path:
                        img_file.rename(new_path)
                        img_file = new_path
                    index[short_id] = {"member": member_name}
                    indexed_keys.add(f"{member_name}/{short_id}")
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


def _add_to_index(group_id: str, member: str) -> str:
    """向索引中添加一条记录，返回生成的短ID。图片需保存为 images/{member}/{short_id}.png"""
    index = _load_index(group_id)
    existing_ids = set(index.keys())
    short_id = _generate_short_id(existing_ids)
    index[short_id] = {"member": member}
    _save_index(group_id, index)
    return short_id


def _id_from_image_path(group_id: str, member: str, filepath: Path) -> str | None:
    """从图片路径推断 short_id：新格式直接取 stem，旧格式按 filename 查找"""
    index = _load_index(group_id)
    short_id = filepath.stem
    if short_id in index and index[short_id].get("member") == member:
        return short_id
    for sid, entry in index.items():
        if entry.get("member") == member and entry.get("filename") == filepath.name:
            return sid
    return None


def _get_members_with_quotes(group_id: str) -> list[str]:
    """返回有语录的群友主昵称列表（用于等概率随机群友），排除已删除群友"""
    members = _load_members(group_id)
    deleted = _load_deleted_members(group_id)
    result = []
    for name in members:
        if name in deleted:
            continue
        image_dir = _get_member_image_dir(group_id, name)
        if image_dir.exists() and list(image_dir.glob("*.*")):
            result.append(name)
    return result


async def _extract_images(
    bot: Bot, event: GroupMessageEvent, args: Message
) -> list[MessageSegment]:
    """从命令参数和引用消息中提取图片，支持多张图片（含引用）依次处理"""
    images: list[MessageSegment] = []
    # 1. 命令参数中的图片
    for seg in args:
        if seg.type == "image":
            images.append(seg)
    # 2. 引用消息中的图片
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


# ==================== 注册命令 ====================
add_member_cmd = on_command("新增群友", priority=10, block=True)
add_alias_cmd = on_command("新增别名", priority=10, block=True)
list_members_cmd = on_command("群友列表", priority=10, block=True)
upload_cmd = on_command("上传", priority=10, block=True)


def _upload_image_first_rule(event: GroupMessageEvent) -> bool:
    """仅当消息首段为非文本（如图片）且包含上传命令时触发，用于支持「图片在上指令在下」"""
    msg = event.get_message()
    if not msg or msg[0].is_text():
        return False  # 首段是文本时由 on_command 处理
    text = msg.extract_plain_text().strip()
    if not text:
        return False
    # 匹配 /上传 或 .上传 等，排除「截图上传」
    if not re.search(r"[/.\!！]上传(?:\s|$)", text):
        return False
    # 消息中需有图片或引用含图
    has_image = any(seg.type == "image" for seg in msg)
    if not has_image and not (
        event.reply
        and event.reply.message
        and any(seg.type == "image" for seg in event.reply.message)
    ):
        return False
    return True


upload_image_first_cmd = on_message(
    _upload_image_first_rule, priority=9, block=True
)  # priority 略低于 on_command，但 on_command 在首段非文本时不会匹配


screenshot_upload_cmd = on_command("截图上传", priority=10, block=True)
view_cmd = on_command("查看", priority=10, block=True)
random_member_cmd = on_command("随机群友", priority=10, block=True)
random_quote_cmd = on_command("随机语录", priority=10, block=True)
random_essence_cmd = on_command("随机精华", priority=10, block=True)
delete_quote_cmd = on_command(
    "删除语录", priority=10, block=True, permission=SUPERUSER
)
delete_member_cmd = on_command(
    "删除群友", priority=10, block=True, permission=SUPERUSER
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


def _merge_member_quotes_into(
    group_id: str, from_member: str, to_member: str
) -> int:
    """将 from_member 下的语录移动到 to_member 下，更新 index，返回移动条数。"""
    index = _load_index(group_id)
    from_dir = _get_member_image_dir(group_id, from_member)
    to_dir = _get_member_image_dir(group_id, to_member)
    moved = 0
    for short_id, entry in list(index.items()):
        if entry.get("member") != from_member:
            continue
        fn = entry.get("filename") or f"{short_id}.png"
        src = from_dir / fn
        if not src.exists():
            continue
        to_dir.mkdir(parents=True, exist_ok=True)
        dst = to_dir / fn
        if dst.exists() and dst != src:
            dst.unlink()
        src.rename(dst)
        index[short_id] = {"member": to_member, "filename": fn}
        moved += 1
    if moved > 0:
        _save_index(group_id, index)
    if from_dir.exists():
        try:
            from_dir.rmdir()
        except OSError:
            pass
    return moved


@add_alias_cmd.handle()
async def handle_add_alias(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /新增别名 命令。若 A、B 均存在，则把 B 当作 A 的别名，并将 B 下的语录合并到 A。"""
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

    if canonical == alias:
        await add_alias_cmd.finish("主昵称与别名不能相同")

    members = _load_members(group_id)
    aliases = _load_aliases(group_id)

    if alias in aliases:
        await add_alias_cmd.finish(
            f"「{alias}」已是群友「{aliases[alias]}」的别名"
        )

    if alias in members:
        # B 是主昵称：把 B 当作 A 的别名，合并 B 的语录到 A
        alias_canonical = alias
        moved = _merge_member_quotes_into(group_id, alias_canonical, canonical)
        # 将 B 的别名全部改为指向 A
        for a, c in list(aliases.items()):
            if c == alias_canonical:
                aliases[a] = canonical
        aliases[alias] = canonical
        members.remove(alias)
        _save_members(group_id, members)
        _save_aliases(group_id, aliases)
        deleted = _load_deleted_members(group_id)
        if alias in deleted:
            deleted.discard(alias)
            _save_deleted_members(group_id, deleted)
        msg = f"已将群友「{alias}」设为「{canonical}」的别名"
        if moved > 0:
            msg += f"，并合并了 {moved} 条语录到「{canonical}」"
        await add_alias_cmd.finish(msg + "✓")
    else:
        # B 不是主昵称：常规添加别名
        aliases[alias] = canonical
        _save_aliases(group_id, aliases)
        await add_alias_cmd.finish(f"已为群友「{canonical}」添加别名「{alias}」✓")


@list_members_cmd.handle()
async def handle_list_members(bot: Bot, event: GroupMessageEvent):
    """处理 /群友列表 命令：以合并转发（聊天记录）形式发送，避免刷屏"""
    group_id = str(event.group_id)
    members = _load_members(group_id)
    deleted = _load_deleted_members(group_id)

    visible = [m for m in members if m not in deleted]
    if not visible:
        await list_members_cmd.finish(
            "本群还没有记录任何群友，使用 /新增群友 <昵称> 来添加吧"
        )

    aliases = _load_aliases(group_id)
    alias_map: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        alias_map.setdefault(canonical, []).append(alias)

    lines = []
    idx = 0
    for name in visible:
        idx += 1
        image_dir = _get_member_image_dir(group_id, name)
        count = len(list(image_dir.glob("*.*"))) if image_dir.exists() else 0
        alias_list = alias_map.get(name, [])
        alias_str = f"（{'、'.join(alias_list)}）" if alias_list else ""
        lines.append(f"  {idx}. {name}{alias_str}：{count}条")
    member_list = "\n".join(lines)
    text = f"本群已记录的群友：\n{member_list}"

    try:
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "YiyinBot")
        bot_uin = str(bot.self_id)
    except Exception:
        bot_name = "YiyinBot"
        bot_uin = str(bot.self_id)

    nodes = [
        {
            "type": "node",
            "data": {
                "name": bot_name,
                "uin": bot_uin,
                "content": Message(MessageSegment.text(text)),
            },
        }
    ]
    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    await list_members_cmd.finish()


async def _do_upload(
    bot: Bot,
    event: GroupMessageEvent,
    name: str,
    args: Message,
    matcher: Matcher,
) -> None:
    """上传语录的核心逻辑，供 on_command 与 on_message（图片在上）共用"""
    if not name:
        await matcher.finish("请输入群友昵称并附带图片，例如：/上传 小明 [图片]")

    group_id = str(event.group_id)
    deleted = _load_deleted_members(group_id)
    canonical = _resolve_name(group_id, name, exclude_deleted=False)
    if canonical and canonical in deleted:
        await matcher.finish(f"群友「{canonical}」已被删除，无法上传")

    auto_registered = False
    if not canonical:
        members = _load_members(group_id)
        members.append(name)
        _save_members(group_id, members)
        canonical = name
        auto_registered = True

    images = await _extract_images(bot, event, args)
    if not images:
        await matcher.finish(
            "请在命令中附带图片或引用含图片的消息，例如：/上传 小明 [图片]"
        )

    # 先下载全部图片，任一出错则不改动任何 JSON
    image_dir = _get_member_image_dir(group_id, canonical)
    image_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[tuple[str, bytes]] = []  # (short_id, content)

    index = _load_index(group_id)
    existing_ids = set(index.keys())
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
                logger.exception(f"下载语录图片失败: {url}")
                if auto_registered:
                    members = _load_members(group_id)
                    members.remove(canonical)
                    _save_members(group_id, members)
                await matcher.finish(
                    f"图片下载失败，已回滚（未修改数据），请稍后重试"
                )

    if not downloaded:
        if auto_registered:
            members = _load_members(group_id)
            members.remove(canonical)
            _save_members(group_id, members)
        await matcher.finish("未获取到有效图片，请检查后重试")

    # 全部下载成功，再写入 index 和文件（任一步失败则回滚）
    try:
        for short_id, content in downloaded:
            index[short_id] = {"member": canonical}
            filepath = image_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存语录文件失败，回滚")
        for short_id, _ in downloaded:
            (image_dir / f"{short_id}.png").unlink(missing_ok=True)
        if auto_registered:
            members = _load_members(group_id)
            members.remove(canonical)
            _save_members(group_id, members)
        await matcher.finish("保存失败，已回滚（未修改数据），请稍后重试")

    prefix = f"群友「{canonical}」已自动注册，" if auto_registered else ""
    id_str = "、".join(sid for sid, _ in downloaded)
    await matcher.finish(
        f"{prefix}已成功为群友「{canonical}」保存 {len(downloaded)} 张语录截图✓\n"
        f"语录ID：{id_str}"
    )


@upload_cmd.handle()
async def handle_upload(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /上传 命令：支持多张图片（含引用），全部成功才写入，任一出错则回滚"""
    name = args.extract_plain_text().strip()
    await _do_upload(bot, event, name, args, upload_cmd)


@upload_image_first_cmd.handle()
async def handle_upload_image_first(bot: Bot, event: GroupMessageEvent):
    """处理「图片在上、指令在下」的 /上传：NoneBot 命令匹配首段须为文本，首段为图时需额外处理"""
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    m = re.search(r"[/.\!！]上传\s*(.*)", text)
    name = m.group(1).strip() if m and m.group(1) else ""
    await _do_upload(bot, event, name, msg, upload_image_first_cmd)


# 从消息文本中解析语录 ID 的正则
_QUOTE_ID_PREFIX_RE = re.compile(r"语录ID[：:]\s*([A-Za-z0-9]+(?:\s*[、]\s*[A-Za-z0-9]+)*)")
_QUOTE_LABEL_RE = re.compile(r"「([A-Za-z0-9]+)」【对应截图】")
_QUOTE_ID_HINT_RE = re.compile(r"[（(]ID[：:]\s*([A-Za-z0-9]+)[）)]")


def _parse_quote_ids_from_text(text: str) -> list[str]:
    """从消息文本中解析语录 ID 列表。支持格式：语录ID：xxx、yyy；「id」【对应截图】；（ID：xxx）"""
    ids: list[str] = []
    # 1. 语录ID：Ab3x9K 或 语录ID：Ab3x9K、Bc4y2L
    m = _QUOTE_ID_PREFIX_RE.search(text)
    if m:
        part = m.group(1)
        for sid in re.split(r"\s*[、]\s*", part):
            sid = sid.strip()
            if sid and sid not in ids:
                ids.append(sid)
    # 2. 「id」【对应截图】格式
    for m in _QUOTE_LABEL_RE.finditer(text):
        sid = m.group(1).strip()
        if sid and sid not in ids:
            ids.append(sid)
    # 3. （ID：xxx）格式
    for m in _QUOTE_ID_HINT_RE.finditer(text):
        sid = m.group(1).strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


async def _extract_quote_ids_from_reply(
    bot: Bot, event: GroupMessageEvent
) -> list[str]:
    """从引用的消息中提取语录 ID（一般为机器人发的语录相关消息）。无引用或解析失败返回空列表。"""
    if not event.reply:
        return []
    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw = msg_data.get("message", [])
        if isinstance(raw, str):
            text = Message(raw).extract_plain_text()
        elif isinstance(raw, list):
            text = "".join(
                s.get("data", {}).get("text", "")
                for s in raw
                if isinstance(s, dict) and s.get("type") == "text"
            )
        else:
            return []
        return _parse_quote_ids_from_text(text.strip())
    except Exception:
        return []


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
    """处理 /截图上传 命令：引用消息生成聊天截图并保存为语录，出错则回滚"""
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

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(
                f"http://q1.qlogo.cn/g?b=qq&nk={sender_id}&s=100", timeout=10
            )
            resp.raise_for_status()
            avatar_bytes = resp.content
        except Exception:
            logger.exception(f"下载头像失败: {sender_id}")
            avatar_bytes = b""

    try:
        screenshot_bytes = await async_generate_chat_screenshot(
            avatar_bytes, sender_nick, reply_text
        )
    except Exception:
        logger.exception("生成截图失败")
        await screenshot_upload_cmd.finish("生成截图失败，请稍后重试")

    deleted = _load_deleted_members(group_id)
    canonical = _resolve_name(group_id, name, exclude_deleted=False)
    if canonical and canonical in deleted:
        await screenshot_upload_cmd.finish(f"群友「{canonical}」已被删除，无法上传")

    auto_registered = False
    if not canonical:
        members = _load_members(group_id)
        members.append(name)
        _save_members(group_id, members)
        canonical = name
        auto_registered = True

    image_dir = _get_member_image_dir(group_id, canonical)
    image_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(group_id)
    short_id = _generate_short_id(set(index.keys()))
    try:
        index[short_id] = {"member": canonical}
        filepath = image_dir / f"{short_id}.png"
        filepath.write_bytes(screenshot_bytes)
        _save_index(group_id, index)
    except Exception:
        logger.exception("保存截图失败，回滚")
        (image_dir / f"{short_id}.png").unlink(missing_ok=True)
        if auto_registered:
            members = _load_members(group_id)
            members.remove(canonical)
            _save_members(group_id, members)
        await screenshot_upload_cmd.finish("保存失败，已回滚（未修改数据），请稍后重试")

    prefix = f"群友「{canonical}」已自动注册，" if auto_registered else ""
    msg = MessageSegment.text(
        f"{prefix}已为群友「{canonical}」生成并保存截图✓\n语录ID：{short_id}\n"
    ) + MessageSegment.image(screenshot_bytes)
    await screenshot_upload_cmd.finish(msg)


def _message_to_content(msg: Message) -> list[dict]:
    """Message 转为 OneBot API 可序列化的 content 格式"""
    result = []
    for seg in msg:
        data = {k: v for k, v in seg.data.items() if v is not None}
        result.append({"type": seg.type, "data": data})
    return result


def _make_forward_node(name: str, uin: str, content: Message) -> dict:
    """构建合并转发节点"""
    return {
        "type": "node",
        "data": {"name": name, "uin": uin, "content": _message_to_content(content)},
    }


@view_cmd.handle()
async def handle_view(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /查看 命令：以合并转发形式发送该群友的全部语录，格式为「ID」【对应截图】"""
    name = args.extract_plain_text().strip()
    if not name:
        await view_cmd.finish("请输入群友昵称，例如：/查看 小明")

    group_id = str(event.group_id)
    canonical = _resolve_name(group_id, name, exclude_deleted=True)

    if not canonical:
        await view_cmd.finish(
            f"群友「{name}」不存在，请先使用 /新增群友 {name} 添加"
        )

    index = _load_index(group_id)
    member_entries = [
        (short_id, entry)
        for short_id, entry in index.items()
        if entry.get("member") == canonical
    ]
    if not member_entries:
        await view_cmd.finish(
            f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
        )

    try:
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "YiyinBot")
        bot_uin = str(bot.self_id)
    except Exception:
        bot_name = "YiyinBot"
        bot_uin = str(bot.self_id)

    # 使用本地文件路径构建节点，不加载图片到内存；单条合并转发最多 200 节点
    images_dir = _get_group_dir(group_id) / "images" / canonical
    nodes = []
    for short_id, entry in member_entries:
        fn = entry.get("filename") or f"{short_id}.png"
        filepath = images_dir / fn
        if not filepath.exists():
            continue
        try:
            img_seg = image_segment_from_path(filepath)
        except Exception:
            logger.exception(f"读取语录图片失败: {filepath}")
            continue
        content = Message(
            MessageSegment.text(f"「{short_id}」【对应截图】\n")
        ) + img_seg
        nodes.append(_make_forward_node(bot_name, bot_uin, content))

    if not nodes:
        await view_cmd.finish(
            f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
        )

    chunk_size = 200
    for i in range(0, len(nodes), chunk_size):
        chunk = nodes[i : i + chunk_size]
        await bot.send_group_forward_msg(group_id=event.group_id, messages=chunk)
    await view_cmd.finish()


@random_member_cmd.handle()
async def handle_random_member(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /随机群友 [昵称]：等概率随机一个有语录的群友再随机一条；指定昵称则从该群友语录中随机"""
    group_id = str(event.group_id)
    name_arg = args.extract_plain_text().strip()

    if name_arg:
        # 指定了昵称：从该群友的语录中随机一条
        canonical = _resolve_name(group_id, name_arg, exclude_deleted=True)
        if not canonical:
            await random_member_cmd.finish(
                f"群友「{name_arg}」不存在，请先使用 /新增群友 {name_arg} 添加"
            )
        image_dir = _get_member_image_dir(group_id, canonical)
        if not image_dir.exists():
            await random_member_cmd.finish(
                f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
            )
        image_files = list(image_dir.glob("*.*"))
        if not image_files:
            await random_member_cmd.finish(
                f"群友「{canonical}」还没有语录记录，使用 /上传 {canonical} [图片] 来添加吧"
            )
        chosen_name = canonical
        chosen_file = random.choice(image_files)
    else:
        # 未指定昵称：先等概率随机一个有语录的群友，再从其语录中随机一条
        members_with = _get_members_with_quotes(group_id)
        if not members_with:
            await random_member_cmd.finish(
                "本群还没有任何语录记录，使用 /上传 <昵称> [图片] 来添加吧"
            )
        chosen_name = random.choice(members_with)
        image_dir = _get_member_image_dir(group_id, chosen_name)
        image_files = list(image_dir.glob("*.*"))
        chosen_file = random.choice(image_files)

    image_bytes = chosen_file.read_bytes()
    short_id = _id_from_image_path(group_id, chosen_name, chosen_file)
    id_hint = f"（ID：{short_id}）" if short_id else ""
    msg = MessageSegment.text(
        f"随机抽到了群友「{chosen_name}」的语录{id_hint}：\n"
    ) + MessageSegment.image(image_bytes)
    await random_member_cmd.finish(msg)


@random_quote_cmd.handle()
async def handle_random_quote(bot: Bot, event: GroupMessageEvent):
    """处理 /随机语录 命令：从本群全部语录中随机抽取一条"""
    group_id = str(event.group_id)
    members = _load_members(group_id)
    deleted = _load_deleted_members(group_id)

    if not members:
        await random_quote_cmd.finish(
            "本群还没有记录任何群友，使用 /新增群友 <昵称> 来添加吧"
        )

    all_quotes: list[tuple[str, Path]] = []
    for name in members:
        if name in deleted:
            continue
        image_dir = _get_member_image_dir(group_id, name)
        if image_dir.exists():
            for img_file in image_dir.glob("*.*"):
                all_quotes.append((name, img_file))

    if not all_quotes:
        await random_quote_cmd.finish(
            "本群还没有任何语录记录，使用 /上传 <昵称> [图片] 来添加吧"
        )

    chosen_name, chosen_file = random.choice(all_quotes)
    image_bytes = chosen_file.read_bytes()

    short_id = _id_from_image_path(group_id, chosen_name, chosen_file)
    id_hint = f"（ID：{short_id}）" if short_id else ""
    msg = MessageSegment.text(
        f"随机抽到了群友「{chosen_name}」的语录{id_hint}：\n"
    ) + MessageSegment.image(image_bytes)
    await random_quote_cmd.finish(msg)


def _msg_to_plain_text(raw) -> str:
    """将 get_msg 返回的 message 转为纯文本"""
    if isinstance(raw, str):
        return Message(raw).extract_plain_text().strip()
    if isinstance(raw, Message):
        return raw.extract_plain_text().strip()
    if isinstance(raw, list):
        parts = []
        for seg in raw:
            if isinstance(seg, dict):
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
                elif seg.get("type") in ("image", "face", "record", "video"):
                    parts.append(f"[{seg.get('type', 'message')}]")
            elif isinstance(seg, MessageSegment) and seg.type == "text":
                parts.append(seg.data.get("text", ""))
        return "".join(parts).strip()
    return ""


@random_essence_cmd.handle()
async def handle_random_essence(bot: Bot, event: GroupMessageEvent):
    """处理 /随机精华 命令：从群精华消息中随机抽一条，发送格式为 昵称：内容"""
    group_id = event.group_id
    try:
        result = await bot.call_api("get_essence_msg_list", group_id=group_id)
    except Exception as e:
        logger.warning(f"获取群精华消息列表失败: {e}")
        await random_essence_cmd.finish(
            "获取精华列表失败，请确认当前协议支持 get_essence_msg_list（如 go-cqhttp）且机器人有权限。"
        )

    if not result:
        await random_essence_cmd.finish("本群暂无精华消息。")

    essence_list = result if isinstance(result, list) else []
    if not essence_list:
        await random_essence_cmd.finish("本群暂无精华消息。")

    # 打乱顺序后逐条尝试，避免抽到已过期/已删除的消息时直接失败
    shuffled = list(essence_list)
    random.shuffle(shuffled)
    last_error = None

    for chosen in shuffled:
        msg_id = chosen.get("message_id")
        sender_nick = chosen.get("sender_nick") or "未知"
        if msg_id is None:
            continue
        try:
            msg_data = await bot.get_msg(message_id=msg_id)
        except Exception as e:
            last_error = e
            # retcode 1200 = 消息不存在（已过期或已删除），换下一条重试
            if getattr(e, "retcode", None) == 1200 or "消息不存在" in str(e):
                logger.debug(f"精华消息已不可用 message_id={msg_id}，尝试下一条")
                continue
            logger.warning(f"获取精华消息内容失败 message_id={msg_id}: {e}")
            await random_essence_cmd.finish("获取精华消息内容失败，请稍后重试。")

        raw = msg_data.get("message", "")
        content = _msg_to_plain_text(raw)
        if not content:
            content = "[该条消息无文字内容]"
        await random_essence_cmd.finish(f"{sender_nick}：{content}")

    logger.warning(f"所有精华消息均不可用（如已过期或已删除）: {last_error}")
    await random_essence_cmd.finish(
        "随机到的精华消息已过期或已被删除，无法获取内容，请稍后再试。"
    )


@delete_quote_cmd.handle()
async def handle_delete_quote(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /删除语录 命令（仅超级管理员），支持引用语录消息后 /删除语录 自动提取 ID"""
    quote_id = args.extract_plain_text().strip()
    if not quote_id and event.reply:
        reply_ids = await _extract_quote_ids_from_reply(bot, event)
        if len(reply_ids) == 1:
            quote_id = reply_ids[0]
    if not quote_id:
        await delete_quote_cmd.finish(
            "请输入要删除的语录ID，例如：/删除语录 Ab3x9K\n"
            "或引用语录消息后直接发送 /删除语录 自动提取 ID"
        )

    group_id = str(event.group_id)
    index = _load_index(group_id)

    if quote_id not in index:
        await delete_quote_cmd.finish(f"语录ID「{quote_id}」不存在，请检查后重试")

    entry = index[quote_id]
    member = entry["member"]
    fn = entry.get("filename") or f"{quote_id}.png"
    filepath = _get_member_image_dir(group_id, member) / fn
    if filepath.exists():
        filepath.unlink()

    del index[quote_id]
    _save_index(group_id, index)

    await delete_quote_cmd.finish(
        f"已删除群友「{member}」的语录（ID：{quote_id}）✓"
    )


@delete_member_cmd.handle()
async def handle_delete_member(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /删除群友 命令（仅超级管理员）：打上删除标记，此后不再显示，但不清理 data"""
    name = args.extract_plain_text().strip()
    if not name:
        await delete_member_cmd.finish(
            "请输入要删除的群友昵称，例如：/删除群友 小明"
        )

    group_id = str(event.group_id)
    canonical = _resolve_name(group_id, name, exclude_deleted=False)
    if not canonical:
        await delete_member_cmd.finish(
            f"群友「{name}」不存在，请检查昵称或别名后重试"
        )

    deleted = _load_deleted_members(group_id)
    if canonical in deleted:
        await delete_member_cmd.finish(f"群友「{canonical}」已被标记删除")

    deleted.add(canonical)
    _save_deleted_members(group_id, deleted)
    await delete_member_cmd.finish(
        f"已为群友「{canonical}」打上删除标记，此后所有调用不再显示✓"
    )
