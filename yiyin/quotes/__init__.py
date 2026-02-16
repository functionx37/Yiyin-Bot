"""
NoneBot2 群友语录插件
- 命令：/新增群友 <群友昵称>
- 命令：/群友列表
- 命令：/上传 <群友昵称> [图片]
- 命令：/看 <群友昵称>
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


# ==================== 注册命令 ====================
add_member_cmd = on_command("新增群友", priority=10, block=True)
list_members_cmd = on_command("群友列表", priority=10, block=True)
upload_cmd = on_command("上传", priority=10, block=True)
view_cmd = on_command("看", priority=10, block=True)


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

    members.append(name)
    _save_members(group_id, members)
    await add_member_cmd.finish(f"已成功添加群友「{name}」✓")


@list_members_cmd.handle()
async def handle_list_members(bot: Bot, event: GroupMessageEvent):
    """处理 /群友列表 命令"""
    group_id = str(event.group_id)
    members = _load_members(group_id)

    if not members:
        await list_members_cmd.finish(
            "本群还没有记录任何群友，使用 /新增群友 <昵称> 来添加吧"
        )

    member_list = "\n".join(f"  {i + 1}. {name}" for i, name in enumerate(members))
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
    members = _load_members(group_id)

    if name not in members:
        await upload_cmd.finish(
            f"群友「{name}」不存在，请先使用 /新增群友 {name} 添加"
        )

    # 从消息中提取图片
    images = [seg for seg in args if seg.type == "image"]
    if not images:
        await upload_cmd.finish("请在命令中附带图片，例如：/上传 小明 [图片]")

    # 下载并保存图片
    image_dir = _get_member_image_dir(group_id, name)
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

    await upload_cmd.finish(
        f"已成功为群友「{name}」保存 {saved_count} 张语录截图✓"
    )


@view_cmd.handle()
async def handle_view(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /看 命令"""
    name = args.extract_plain_text().strip()
    if not name:
        await view_cmd.finish("请输入群友昵称，例如：/看 小明")

    group_id = str(event.group_id)
    members = _load_members(group_id)

    if name not in members:
        await view_cmd.finish(
            f"群友「{name}」不存在，请先使用 /新增群友 {name} 添加"
        )

    image_dir = _get_member_image_dir(group_id, name)
    if not image_dir.exists():
        await view_cmd.finish(
            f"群友「{name}」还没有语录记录，使用 /上传 {name} [图片] 来添加吧"
        )

    image_files = list(image_dir.glob("*.*"))
    if not image_files:
        await view_cmd.finish(
            f"群友「{name}」还没有语录记录，使用 /上传 {name} [图片] 来添加吧"
        )

    # 随机抽取一张
    chosen = random.choice(image_files)
    image_bytes = chosen.read_bytes()

    msg = MessageSegment.text(f"群友「{name}」的语录：\n") + MessageSegment.image(
        image_bytes
    )
    await view_cmd.finish(msg)
