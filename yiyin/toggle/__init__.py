"""
NoneBot2 功能开关管理插件
- 命令：/功能列表        — 查看当前群所有功能的启用/禁用状态
- 命令：/启用 <功能名>   — 在当前群启用指定功能（仅管理员/群主）
- 命令：/禁用 <功能名>   — 在当前群禁用指定功能（仅管理员/群主）
- 原理：通过 run_preprocessor 全局拦截，对已禁用的插件直接忽略
"""

import json
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "data" / "toggle" / "config.json"

# ==================== 插件注册表 ====================
# key: 插件模块名（yiyin/ 下的目录名）
# value: 用户可见的中文功能名
# 新增插件时，在此处添加一行即可纳入开关管理
PLUGIN_REGISTRY: dict[str, str] = {
    "tarot": "塔罗牌",
    "quotes": "群友语录",
    "symmetric": "对称图片",
    "wolfram": "数学求解",
}

# 反向映射：中文功能名 -> 模块名（用于命令参数解析）
_DISPLAY_TO_MODULE: dict[str, str] = {v: k for k, v in PLUGIN_REGISTRY.items()}

# 本插件名称，不可被禁用
_SELF_PLUGIN = "toggle"

# ==================== 内存缓存 ====================
_config_cache: dict | None = None


def _load_config() -> dict:
    """加载配置（优先使用内存缓存，首次从文件读取）"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not CONFIG_PATH.exists():
        _config_cache = {"disabled": {}}
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)

    return _config_cache


def _save_config(config: dict) -> None:
    """保存配置到文件并更新内存缓存"""
    global _config_cache
    _config_cache = config
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _is_disabled(plugin_key: str, group_id: str) -> bool:
    """检查指定插件是否在指定群被禁用"""
    config = _load_config()
    disabled_list = config.get("disabled", {}).get(group_id, [])
    return plugin_key in disabled_list


def _get_plugin_key(matcher: Matcher) -> str | None:
    """从 Matcher 提取插件模块名（兼容多种命名格式）"""
    plugin = matcher.plugin
    if plugin is None:
        return None
    name = plugin.name
    # NoneBot2 可能使用 "yiyin.tarot" 或 "yiyin:tarot" 等格式
    for sep in (".", ":"):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return name


# ==================== 全局预处理器 ====================
@run_preprocessor
async def toggle_check(matcher: Matcher, event: Event):
    """在每个 Matcher 执行前检查功能开关"""
    # 仅处理群消息
    if not isinstance(event, GroupMessageEvent):
        return

    plugin_key = _get_plugin_key(matcher)

    # 本插件或未注册插件不做拦截
    if plugin_key is None or plugin_key == _SELF_PLUGIN:
        return
    if plugin_key not in PLUGIN_REGISTRY:
        return

    group_id = str(event.group_id)
    if _is_disabled(plugin_key, group_id):
        raise IgnoredException(
            f"插件「{PLUGIN_REGISTRY[plugin_key]}」在群 {group_id} 已被禁用"
        )


# ==================== 注册命令 ====================
list_cmd = on_command("功能列表", priority=1, block=True)
enable_cmd = on_command(
    "启用",
    priority=1,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)
disable_cmd = on_command(
    "禁用",
    priority=1,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)


# ==================== 命令处理 ====================
@list_cmd.handle()
async def handle_list(bot: Bot, event: GroupMessageEvent):
    """处理 /功能列表 命令：展示本群所有功能的启用状态"""
    group_id = str(event.group_id)
    config = _load_config()
    disabled = config.get("disabled", {}).get(group_id, [])

    lines = ["「本群功能状态」", ""]
    for key, display_name in PLUGIN_REGISTRY.items():
        status = "❌ 已禁用" if key in disabled else "✅ 已启用"
        lines.append(f"  {display_name}  {status}")

    lines.append("")
    lines.append("管理员可使用：")
    lines.append("  /启用 <功能名>")
    lines.append("  /禁用 <功能名>")

    await list_cmd.finish("\n".join(lines))


@enable_cmd.handle()
async def handle_enable(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /启用 命令：在当前群启用指定功能"""
    name = args.extract_plain_text().strip()
    if not name:
        available = "、".join(PLUGIN_REGISTRY.values())
        await enable_cmd.finish(f"请指定要启用的功能名，可用功能：{available}")

    module_key = _DISPLAY_TO_MODULE.get(name)
    if module_key is None:
        available = "、".join(PLUGIN_REGISTRY.values())
        await enable_cmd.finish(f"未知功能「{name}」，可用功能：{available}")

    group_id = str(event.group_id)
    config = _load_config()
    disabled = config.get("disabled", {}).get(group_id, [])

    if module_key not in disabled:
        await enable_cmd.finish(f"功能「{name}」在本群已经是启用状态")

    disabled.remove(module_key)
    if not disabled:
        # 该群没有禁用项了，清理空列表
        config["disabled"].pop(group_id, None)
    else:
        config["disabled"][group_id] = disabled
    _save_config(config)

    await enable_cmd.finish(f"已在本群启用功能「{name}」✓")


@disable_cmd.handle()
async def handle_disable(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /禁用 命令：在当前群禁用指定功能"""
    name = args.extract_plain_text().strip()
    if not name:
        available = "、".join(PLUGIN_REGISTRY.values())
        await disable_cmd.finish(f"请指定要禁用的功能名，可用功能：{available}")

    module_key = _DISPLAY_TO_MODULE.get(name)
    if module_key is None:
        available = "、".join(PLUGIN_REGISTRY.values())
        await disable_cmd.finish(f"未知功能「{name}」，可用功能：{available}")

    group_id = str(event.group_id)
    config = _load_config()
    disabled = config.setdefault("disabled", {}).setdefault(group_id, [])

    if module_key in disabled:
        await disable_cmd.finish(f"功能「{name}」在本群已经是禁用状态")

    disabled.append(module_key)
    _save_config(config)

    await disable_cmd.finish(f"已在本群禁用功能「{name}」✓")
