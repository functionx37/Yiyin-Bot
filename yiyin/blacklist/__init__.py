"""
NoneBot2 黑名单插件
- 忽略黑名单上的 QQ 号发送的所有指令
- 超级管理员不受黑名单限制，且可管理黑名单
- 命令：/拉黑 <QQ号>、/移除黑名单 <QQ号>、/黑名单列表
"""

import json
from pathlib import Path

from nonebot import get_driver, on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11 import Message

# ==================== 路径与配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BLACKLIST_PATH = PROJECT_ROOT / "data" / "blacklist" / "blacklist.json"

_config_cache: set[str] | None = None


def _load_blacklist() -> set[str]:
    """加载黑名单（QQ 号字符串集合）"""
    global _config_cache
    if not BLACKLIST_PATH.exists():
        _config_cache = set()
        return _config_cache

    try:
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = data.get("user_ids", [])
        _config_cache = set(str(uid) for uid in ids)
        return _config_cache
    except (json.JSONDecodeError, OSError):
        _config_cache = set()
        return _config_cache


def _save_blacklist(user_ids: set[str]) -> None:
    """保存黑名单到文件"""
    global _config_cache
    _config_cache = user_ids
    BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump({"user_ids": sorted(user_ids)}, f, ensure_ascii=False, indent=2)


def _is_superuser(user_id: str) -> bool:
    """判断是否为超级管理员"""
    try:
        su = get_driver().config.superusers
        return user_id in (su or set())
    except Exception:
        return False


# ==================== 事件预处理器 ====================
@event_preprocessor
async def blacklist_filter(event: Event):
    """在事件分发前拦截黑名单用户：忽略其所有消息"""
    try:
        user_id = event.get_user_id()
    except ValueError:
        # 无用户上下文的事件（如 meta、lifecycle 等）直接放行
        return
    if not user_id:
        return

    # 超级管理员不受黑名单限制
    if _is_superuser(user_id):
        return

    blacklist = _load_blacklist()
    if user_id in blacklist:
        raise IgnoredException(f"用户 {user_id} 在黑名单中，已忽略其指令")


# ==================== 注册命令 ====================
add_blacklist_cmd = on_command(
    "拉黑",
    priority=1,
    block=True,
    permission=SUPERUSER,
)
remove_blacklist_cmd = on_command(
    "移除黑名单",
    priority=1,
    block=True,
    permission=SUPERUSER,
)
list_blacklist_cmd = on_command(
    "黑名单列表",
    priority=1,
    block=True,
    permission=SUPERUSER,
)


# ==================== 命令处理 ====================
@add_blacklist_cmd.handle()
async def handle_add(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    """处理 /拉黑 <QQ号>：将用户加入黑名单"""
    raw = args.extract_plain_text().strip()
    if not raw or not raw.isdigit():
        await add_blacklist_cmd.finish("用法：/拉黑 <QQ号>")

    target_id = raw
    if _is_superuser(target_id):
        await add_blacklist_cmd.finish("不能将超级管理员加入黑名单")

    blacklist = _load_blacklist()
    if target_id in blacklist:
        await add_blacklist_cmd.finish(f"QQ {target_id} 已在黑名单中")

    blacklist.add(target_id)
    _save_blacklist(blacklist)
    await add_blacklist_cmd.finish(f"已将 QQ {target_id} 加入黑名单 ✓")


@remove_blacklist_cmd.handle()
async def handle_remove(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
    args: Message = CommandArg(),
):
    """处理 /移除黑名单 <QQ号>：将用户移出黑名单"""
    raw = args.extract_plain_text().strip()
    if not raw or not raw.isdigit():
        await remove_blacklist_cmd.finish("用法：/移除黑名单 <QQ号>")

    target_id = raw
    blacklist = _load_blacklist()
    if target_id not in blacklist:
        await remove_blacklist_cmd.finish(f"QQ {target_id} 不在黑名单中")

    blacklist.discard(target_id)
    _save_blacklist(blacklist)
    await remove_blacklist_cmd.finish(f"已将 QQ {target_id} 移出黑名单 ✓")


@list_blacklist_cmd.handle()
async def handle_list(
    bot: Bot,
    event: GroupMessageEvent | PrivateMessageEvent,
):
    """处理 /黑名单列表：展示当前黑名单"""
    blacklist = _load_blacklist()
    if not blacklist:
        await list_blacklist_cmd.finish("黑名单为空")

    ids = sorted(blacklist)
    lines = ["「黑名单」", ""] + [f"  • {uid}" for uid in ids]
    lines.append("")
    lines.append("管理员可使用：")
    lines.append("  /拉黑 <QQ号>")
    lines.append("  /移除黑名单 <QQ号>")
    await list_blacklist_cmd.finish("\n".join(lines))
