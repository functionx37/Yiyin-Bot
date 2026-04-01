"""
NoneBot2 idol 图册子功能
- /up <name> [图片]
- /list
- /idol
- name 或 name n（无 / 前缀）
- /del <id>
- /rename <name1> <name2>
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

from yiyin.utils import image_segment_from_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "idol"


def _get_members_file() -> Path:
    return DATA_DIR / "members.json"


def _get_aliases_file() -> Path:
    return DATA_DIR / "aliases.json"


def _get_index_file() -> Path:
    return DATA_DIR / "index.json"


def _get_member_image_dir(member_name: str) -> Path:
    return DATA_DIR / "images" / member_name


def _load_members() -> list[str]:
    fp = _get_members_file()
    if not fp.exists():
        return []
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_members(members: list[str]) -> None:
    fp = _get_members_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)


def _load_aliases() -> dict[str, str]:
    fp = _get_aliases_file()
    if not fp.exists():
        return {}
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_aliases(aliases: dict[str, str]) -> None:
    fp = _get_aliases_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)


def _resolve_name(name: str) -> str | None:
    members = _load_members()
    if name in members:
        return name
    aliases = _load_aliases()
    canonical = aliases.get(name)
    if not canonical or canonical not in members:
        return None
    return canonical


def _generate_short_id(existing_ids: set[str]) -> str:
    chars = string.ascii_letters + string.digits
    while True:
        short_id = "".join(random.choices(chars, k=6))
        if short_id not in existing_ids:
            return short_id


def _save_index(index: dict[str, dict]) -> None:
    fp = _get_index_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _load_index() -> dict[str, dict]:
    fp = _get_index_file()
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    indexed_keys: set[str] = set()
    for short_id, entry in index.items():
        member = entry.get("member")
        if not member:
            continue
        indexed_keys.add(f"{member}/{short_id}")
        fn = entry.get("filename")
        if fn:
            indexed_keys.add(f"{member}/{fn}")

    images_dir = DATA_DIR / "images"
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
        _save_index(index)
    return index


def _id_from_image_path(member: str, filepath: Path) -> str | None:
    index = _load_index()
    short_id = filepath.stem
    if short_id in index and index[short_id].get("member") == member:
        return short_id
    for sid, entry in index.items():
        if entry.get("member") == member and entry.get("filename") == filepath.name:
            return sid
    return None


def _get_members_with_images() -> list[str]:
    members = _load_members()
    result = []
    for name in members:
        image_dir = _get_member_image_dir(name)
        if image_dir.exists() and list(image_dir.glob("*.*")):
            result.append(name)
    return result


async def _extract_images(bot: Bot, event: GroupMessageEvent, args: Message) -> list[MessageSegment]:
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
                            reply_images.append(MessageSegment("image", seg.get("data", {})))
            except Exception:
                pass
        images.extend(reply_images)
    return images


def _message_to_content(msg: Message) -> list[dict]:
    result = []
    for seg in msg:
        data = {k: v for k, v in seg.data.items() if v is not None}
        result.append({"type": seg.type, "data": data})
    return result


def _make_forward_node(name: str, uin: str, content: Message) -> dict:
    return {"type": "node", "data": {"name": name, "uin": uin, "content": _message_to_content(content)}}


async def _get_bot_info(bot: Bot) -> tuple[str, str]:
    try:
        info = await bot.get_login_info()
        return info.get("nickname", "YiyinBot"), str(bot.self_id)
    except Exception:
        return "YiyinBot", str(bot.self_id)


def _merge_member_images_into(from_member: str, to_member: str) -> int:
    index = _load_index()
    from_dir = _get_member_image_dir(from_member)
    to_dir = _get_member_image_dir(to_member)
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
        _save_index(index)
    if from_dir.exists():
        try:
            from_dir.rmdir()
        except OSError:
            pass
    return moved


up_cmd = on_command("up", priority=10, block=True)
list_cmd = on_command("list", priority=10, block=True)
idol_cmd = on_command("idol", priority=10, block=True)
del_cmd = on_command("del", priority=10, block=True)
rename_cmd = on_command("rename", priority=10, block=True)


def _up_image_first_rule(event: GroupMessageEvent) -> bool:
    msg = event.get_message()
    if not msg or msg[0].is_text():
        return False
    text = msg.extract_plain_text().strip()
    if not text:
        return False
    if not re.search(r"[/.\!！]up(?:\s|$)", text):
        return False
    has_image = any(seg.type == "image" for seg in msg)
    if not has_image and not (
        event.reply and event.reply.message and any(seg.type == "image" for seg in event.reply.message)
    ):
        return False
    return True


up_image_first_cmd = on_message(_up_image_first_rule, priority=9, block=True)


_NAME_N_RE = re.compile(r"^\s*(\S+?)(?:\s+(\d+))?\s*$")


def _name_trigger_rule(event: GroupMessageEvent) -> bool:
    msg = event.get_message()
    # 仅纯文本触发，避免误触发含图片/at/回复等消息
    if any(seg.type != "text" for seg in msg):
        return False
    text = msg.extract_plain_text().strip()
    if not text or text.startswith("/"):
        return False
    m = _NAME_N_RE.match(text)
    if not m:
        return False
    name = m.group(1)
    canonical = _resolve_name(name)
    return canonical is not None


name_trigger_cmd = on_message(_name_trigger_rule, priority=10, block=True)


async def _do_upload(bot: Bot, event: GroupMessageEvent, name: str, args: Message, matcher: Matcher) -> None:
    if not name:
        await matcher.finish("请输入名字并附带图片，例如：/up 小明 [图片]")

    canonical = _resolve_name(name)
    auto_registered = False
    if not canonical:
        members = _load_members()
        members.append(name)
        _save_members(members)
        canonical = name
        auto_registered = True

    images = await _extract_images(bot, event, args)
    if not images:
        await matcher.finish("请在命令中附带图片或引用含图片的消息，例如：/up 小明 [图片]")

    image_dir = _get_member_image_dir(canonical)
    image_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[tuple[str, bytes]] = []

    index = _load_index()
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
                logger.exception(f"下载 idol 图片失败: {url}")
                if auto_registered:
                    members = _load_members()
                    members.remove(canonical)
                    _save_members(members)
                await matcher.finish("图片下载失败，已回滚（未修改数据），请稍后重试")

    if not downloaded:
        if auto_registered:
            members = _load_members()
            members.remove(canonical)
            _save_members(members)
        await matcher.finish("未获取到有效图片，请检查后重试")

    try:
        for short_id, content in downloaded:
            index[short_id] = {"member": canonical}
            filepath = image_dir / f"{short_id}.png"
            filepath.write_bytes(content)
        _save_index(index)
    except Exception:
        logger.exception("保存 idol 图片失败，回滚")
        for short_id, _ in downloaded:
            (image_dir / f"{short_id}.png").unlink(missing_ok=True)
        if auto_registered:
            members = _load_members()
            members.remove(canonical)
            _save_members(members)
        await matcher.finish("保存失败，已回滚（未修改数据），请稍后重试")

    prefix = f"名字『{canonical}』已自动注册，" if auto_registered else ""
    id_str = "、".join(sid for sid, _ in downloaded)
    await matcher.finish(f"{prefix}已成功保存 {len(downloaded)} 张图片✓\nID：{id_str}")


@up_cmd.handle()
async def handle_up(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    await _do_upload(bot, event, name, args, up_cmd)


@up_image_first_cmd.handle()
async def handle_up_image_first(bot: Bot, event: GroupMessageEvent):
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    m = re.search(r"[/.\!！]up\s*(.*)", text)
    name = m.group(1).strip() if m and m.group(1) else ""
    await _do_upload(bot, event, name, msg, up_image_first_cmd)


@list_cmd.handle()
async def handle_list(bot: Bot, event: GroupMessageEvent):
    members = _load_members()
    if not members:
        await list_cmd.finish("还没有记录，先用 /up <name> [图片] 上传吧")

    aliases = _load_aliases()
    alias_map: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        alias_map.setdefault(canonical, []).append(alias)

    lines = []
    idx = 0
    for name in members:
        idx += 1
        image_dir = _get_member_image_dir(name)
        count = len(list(image_dir.glob("*.*"))) if image_dir.exists() else 0
        alias_list = alias_map.get(name, [])
        alias_str = f"（{'、'.join(alias_list)}）" if alias_list else ""
        lines.append(f"  {idx}. {name}{alias_str}：{count}张")

    bot_name, bot_uin = await _get_bot_info(bot)
    text = "已记录名单：\n" + "\n".join(lines)
    nodes = [_make_forward_node(bot_name, bot_uin, Message(MessageSegment.text(text)))]
    await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    await list_cmd.finish()


@idol_cmd.handle()
async def handle_idol(bot: Bot, event: GroupMessageEvent):
    members_with = _get_members_with_images()
    if not members_with:
        await idol_cmd.finish("还没有任何图片记录，先用 /up <name> [图片] 上传吧")
    chosen_name = random.choice(members_with)
    image_dir = _get_member_image_dir(chosen_name)
    image_files = list(image_dir.glob("*.*"))
    if not image_files:
        await idol_cmd.finish("还没有任何图片记录，先用 /up <name> [图片] 上传吧")
    chosen_file = random.choice(image_files)
    short_id = _id_from_image_path(chosen_name, chosen_file)
    id_hint = f"（ID：{short_id}）" if short_id else ""
    msg = MessageSegment.text(f"随机抽到了『{chosen_name}』{id_hint}：\n") + image_segment_from_path(chosen_file)
    await idol_cmd.finish(msg)


@name_trigger_cmd.handle()
async def handle_name_trigger(bot: Bot, event: GroupMessageEvent):
    text = event.get_message().extract_plain_text().strip()
    m = _NAME_N_RE.match(text)
    if not m:
        return
    raw_name = m.group(1)
    raw_n = m.group(2)
    canonical = _resolve_name(raw_name)
    if not canonical:
        return

    n = 1
    if raw_n:
        n = max(1, min(int(raw_n), 50))

    image_dir = _get_member_image_dir(canonical)
    image_files = list(image_dir.glob("*.*")) if image_dir.exists() else []
    if not image_files:
        await name_trigger_cmd.finish(f"『{canonical}』还没有图片记录，先用 /up {canonical} [图片] 上传吧")

    if n == 1:
        chosen_file = random.choice(image_files)
        short_id = _id_from_image_path(canonical, chosen_file)
        id_hint = f"（ID：{short_id}）" if short_id else ""
        msg = MessageSegment.text(f"『{canonical}』{id_hint}：\n") + image_segment_from_path(chosen_file)
        await name_trigger_cmd.finish(msg)

    chosen = random.choices(image_files, k=n)
    bot_name, bot_uin = await _get_bot_info(bot)
    nodes = []
    for filepath in chosen:
        short_id = _id_from_image_path(canonical, filepath)
        id_hint = f"（ID：{short_id}）" if short_id else ""
        content = Message(MessageSegment.text(f"『{canonical}』{id_hint}\n")) + image_segment_from_path(filepath)
        nodes.append(_make_forward_node(bot_name, bot_uin, content))

    chunk_size = 200
    for i in range(0, len(nodes), chunk_size):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes[i : i + chunk_size])
    await name_trigger_cmd.finish()


@del_cmd.handle()
async def handle_del(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    quote_id = args.extract_plain_text().strip()
    if not quote_id:
        await del_cmd.finish("请输入要删除的图片 ID，例如：/del Ab3x9K")

    index = _load_index()
    if quote_id not in index:
        await del_cmd.finish(f"ID『{quote_id}』不存在，请检查后重试")

    entry = index[quote_id]
    member = entry["member"]
    fn = entry.get("filename") or f"{quote_id}.png"
    filepath = _get_member_image_dir(member) / fn
    if filepath.exists():
        filepath.unlink()

    del index[quote_id]
    _save_index(index)
    await del_cmd.finish(f"已删除『{member}』的图片（ID：{quote_id}）✓")


@rename_cmd.handle()
async def handle_rename(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    parts = text.split()
    if len(parts) < 2:
        await rename_cmd.finish("请输入两个名字，例如：/rename 小明 明明")

    existing_name, alias = parts[0], parts[1]
    canonical = _resolve_name(existing_name)
    if not canonical:
        await rename_cmd.finish(f"名字『{existing_name}』不存在，请先使用 /up {existing_name} [图片]")
    if canonical == alias:
        await rename_cmd.finish("主名字与别名不能相同")

    members = _load_members()
    aliases = _load_aliases()

    if alias in aliases:
        await rename_cmd.finish(f"『{alias}』已是『{aliases[alias]}』的别名")

    if alias in members:
        alias_canonical = alias
        moved = _merge_member_images_into(alias_canonical, canonical)
        for a, c in list(aliases.items()):
            if c == alias_canonical:
                aliases[a] = canonical
        aliases[alias] = canonical
        members.remove(alias)
        _save_members(members)
        _save_aliases(aliases)
        msg = f"已将『{alias}』设为『{canonical}』的别名"
        if moved > 0:
            msg += f"，并合并了 {moved} 张图片到『{canonical}』"
        await rename_cmd.finish(msg + "✓")

    aliases[alias] = canonical
    _save_aliases(aliases)
    await rename_cmd.finish(f"已为『{canonical}』添加别名『{alias}』✓")

