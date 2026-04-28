"""
NoneBot2 功能开关管理插件
- 命令：/启用 <功能名>   — 在当前群启用指定功能（仅超级管理员）
- 命令：/禁用 <功能名>   — 在当前群禁用指定功能（仅超级管理员）
- 原理：通过 run_preprocessor 全局拦截，每次从磁盘读取群开关文件
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

# ==================== 数据路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOGGLE_TABLE_PATH = PROJECT_ROOT / "config" / "toggle_table.json"
TOGGLE_DEFAULT_PATH = PROJECT_ROOT / "config" / "toggle_default.json"
GROUP_TOGGLE_DIR = PROJECT_ROOT / "data" / "toggle"

_SELF_PLUGIN_PREFIXES = ("toggle", "yiyin.toggle")


def _read_json_file(path: Path, default: Any) -> Any:
    """读取 JSON；文件不存在或格式错误时返回默认值。"""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    """写入 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_toggle_table() -> tuple[dict[str, str], dict[str, list[str]]]:
    """加载功能注册表和功能组。"""
    raw = _read_json_file(TOGGLE_TABLE_PATH, {})
    if not isinstance(raw, dict):
        return {}, {}

    base_plugin_raw = raw.get("base_plugin", {})
    plugin_groups_raw = raw.get("plugin_groups", {})

    base_plugin: dict[str, str] = {}
    if isinstance(base_plugin_raw, dict):
        for key, display_name in base_plugin_raw.items():
            if isinstance(key, str) and isinstance(display_name, str):
                base_plugin[key] = display_name

    known_display_names = set(base_plugin.values())
    plugin_groups: dict[str, list[str]] = {}
    if isinstance(plugin_groups_raw, dict):
        for group_name, members in plugin_groups_raw.items():
            if not isinstance(group_name, str) or not isinstance(members, list):
                continue
            valid_members = [
                member
                for member in members
                if isinstance(member, str) and member in known_display_names
            ]
            if valid_members:
                plugin_groups[group_name] = valid_members

    return base_plugin, plugin_groups


def _load_default_toggles(
    base_plugin: dict[str, str] | None = None,
) -> dict[str, bool]:
    """加载默认开关状态，仅保留已注册功能。"""
    if base_plugin is None:
        base_plugin, _ = _load_toggle_table()

    raw = _read_json_file(TOGGLE_DEFAULT_PATH, {})
    if not isinstance(raw, dict):
        raw = {}

    defaults: dict[str, bool] = {}
    for display_name in base_plugin.values():
        value = raw.get(display_name)
        defaults[display_name] = value if isinstance(value, bool) else False
    return defaults


def _display_to_key_map(base_plugin: dict[str, str] | None = None) -> dict[str, str]:
    """显示名到功能键的反向映射。"""
    if base_plugin is None:
        base_plugin, _ = _load_toggle_table()
    return {display_name: key for key, display_name in base_plugin.items()}


def _group_toggle_path(group_id: str) -> Path:
    return GROUP_TOGGLE_DIR / f"{group_id}.json"


def _normalize_group_toggle(
    raw_data: Any,
    defaults: dict[str, bool],
    created_group_name: str = "",
) -> tuple[dict[str, Any], bool]:
    """规范化单群开关数据，并返回是否需要回写。"""
    need_write = False
    raw_dict = raw_data if isinstance(raw_data, dict) else {}
    if not isinstance(raw_data, dict):
        need_write = True

    group_name = raw_dict.get("group_name")
    if isinstance(group_name, str):
        normalized_group_name = group_name
    else:
        normalized_group_name = created_group_name
        if "group_name" in raw_dict or created_group_name:
            need_write = True

    toggle_raw = raw_dict.get("toggle")
    if not isinstance(toggle_raw, dict):
        toggle_raw = {}
        need_write = True

    normalized_toggle: dict[str, bool] = {}
    for display_name, default_value in defaults.items():
        value = toggle_raw.get(display_name)
        if isinstance(value, bool):
            normalized_toggle[display_name] = value
        else:
            normalized_toggle[display_name] = default_value
            need_write = True

    if set(toggle_raw.keys()) != set(defaults.keys()):
        need_write = True

    return {
        "group_name": normalized_group_name,
        "toggle": normalized_toggle,
    }, need_write


async def _fetch_group_name(bot: Bot, group_id: str) -> str:
    """获取群名，失败时返回空字符串。"""
    try:
        info = await bot.get_group_info(group_id=int(group_id), no_cache=True)
    except Exception:
        return ""
    group_name = info.get("group_name", "")
    return group_name if isinstance(group_name, str) else ""


def _ensure_group_toggle_sync(group_id: str) -> dict[str, Any]:
    """同步读取并修复单群开关文件。缺文件时仅返回默认值，不落盘。"""
    base_plugin, _ = _load_toggle_table()
    defaults = _load_default_toggles(base_plugin)
    path = _group_toggle_path(group_id)

    if not path.exists():
        normalized, _ = _normalize_group_toggle({}, defaults, "")
        return normalized

    raw_data = _read_json_file(path, {})
    normalized, need_write = _normalize_group_toggle(raw_data, defaults)

    if need_write:
        _write_json_file(path, normalized)
    return normalized


async def _ensure_group_toggle_async(bot: Bot, group_id: str) -> dict[str, Any]:
    """异步读取并修复单群开关文件。缺文件时尝试补上群名。"""
    base_plugin, _ = _load_toggle_table()
    defaults = _load_default_toggles(base_plugin)
    path = _group_toggle_path(group_id)

    if path.exists():
        raw_data = _read_json_file(path, {})
        normalized, need_write = _normalize_group_toggle(raw_data, defaults)
    else:
        group_name = await _fetch_group_name(bot, group_id)
        normalized, need_write = _normalize_group_toggle({}, defaults, group_name)
        need_write = True

    if need_write:
        _write_json_file(path, normalized)
    return normalized


def _resolve_display_name(feature_key: str) -> str | None:
    """将功能键解析为显示名。未注册功能返回 None。"""
    base_plugin, _ = _load_toggle_table()
    return base_plugin.get(feature_key)


def is_feature_enabled(feature_key: str, group_id: str) -> bool:
    """检查指定功能在指定群是否已启用。"""
    display_name = _resolve_display_name(feature_key)
    if display_name is None:
        return True

    group_data = _ensure_group_toggle_sync(group_id)
    toggles = group_data.get("toggle", {})
    if not isinstance(toggles, dict):
        return False
    value = toggles.get(display_name)
    return value if isinstance(value, bool) else False


async def is_feature_enabled_async(bot: Bot, feature_key: str, group_id: str) -> bool:
    """异步检查指定功能在指定群是否已启用。"""
    display_name = _resolve_display_name(feature_key)
    if display_name is None:
        return True

    group_data = await _ensure_group_toggle_async(bot, group_id)
    toggles = group_data.get("toggle", {})
    if not isinstance(toggles, dict):
        return False
    value = toggles.get(display_name)
    return value if isinstance(value, bool) else False


def is_plugin_enabled(plugin_key: str, group_id: str) -> bool:
    """兼容旧调用名，行为等同于 is_feature_enabled。"""
    return is_feature_enabled(plugin_key, group_id)


def _match_registered_key(candidate: str, base_plugin: dict[str, str]) -> str | None:
    """按精确匹配和最长前缀匹配注册表中的功能键。"""
    if candidate in base_plugin:
        return candidate

    for key in sorted(base_plugin.keys(), key=len, reverse=True):
        if candidate.startswith(f"{key}.") or candidate.startswith(f"{key}:"):
            return key
    return None


def _resolve_feature_key(matcher: Matcher) -> str | None:
    """从 Matcher 推断当前触发的基础功能键。"""
    plugin = matcher.plugin
    if plugin is None:
        return None

    plugin_name = plugin.name
    if any(
        plugin_name == prefix or plugin_name.startswith(f"{prefix}.")
        for prefix in _SELF_PLUGIN_PREFIXES
    ):
        return None

    base_plugin, _ = _load_toggle_table()
    if not base_plugin:
        return None

    for handler in matcher.handlers:
        module_name = getattr(handler, "__module__", None)
        if isinstance(module_name, str):
            matched = _match_registered_key(module_name, base_plugin)
            if matched is not None:
                return matched

    return _match_registered_key(plugin_name, base_plugin)


def _resolve_targets(name: str) -> tuple[str, list[str]] | None:
    """将命令参数解析为单个功能或功能组。"""
    base_plugin, plugin_groups = _load_toggle_table()
    display_to_key = _display_to_key_map(base_plugin)

    if name in display_to_key:
        return name, [name]

    members = plugin_groups.get(name)
    if members:
        valid_members = [member for member in members if member in display_to_key]
        if valid_members:
            return name, valid_members
    return None


def _set_feature_states(
    group_data: dict[str, Any],
    target_names: list[str],
    enabled: bool,
) -> bool:
    """设置目标功能状态；有任意变更时返回 True。"""
    toggles = group_data.get("toggle", {})
    if not isinstance(toggles, dict):
        return False

    changed = False
    for name in target_names:
        if toggles.get(name) is not enabled:
            toggles[name] = enabled
            changed = True
    return changed


# ==================== 全局预处理器 ====================
@run_preprocessor
async def toggle_check(bot: Bot, matcher: Matcher, event: Event):
    """在每个 Matcher 执行前检查功能开关。"""
    if not isinstance(event, GroupMessageEvent):
        return

    feature_key = _resolve_feature_key(matcher)
    if feature_key is None:
        return

    group_id = str(event.group_id)
    if await is_feature_enabled_async(bot, feature_key, group_id):
        return

    display_name = _resolve_display_name(feature_key) or feature_key
    raise IgnoredException(f"功能『{display_name}』在群 {group_id} 已被禁用")


# ==================== 注册命令 ====================
enable_cmd = on_command("启用", priority=1, block=True, permission=SUPERUSER)
disable_cmd = on_command("禁用", priority=1, block=True, permission=SUPERUSER)


# ==================== 命令处理 ====================
@enable_cmd.handle()
async def handle_enable(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /启用 命令。"""
    name = args.extract_plain_text().strip()
    if not name:
        return

    resolved = _resolve_targets(name)
    if resolved is None:
        return

    display_name, target_names = resolved
    group_id = str(event.group_id)
    group_data = await _ensure_group_toggle_async(bot, group_id)

    changed = _set_feature_states(group_data, target_names, True)
    if changed:
        _write_json_file(_group_toggle_path(group_id), group_data)
        await enable_cmd.finish(f"已在本群启用功能『{display_name}』✓")

    await enable_cmd.finish(f"功能『{display_name}』在本群已经是启用状态")


@disable_cmd.handle()
async def handle_disable(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    """处理 /禁用 命令。"""
    name = args.extract_plain_text().strip()
    if not name:
        return

    resolved = _resolve_targets(name)
    if resolved is None:
        return

    display_name, target_names = resolved
    group_id = str(event.group_id)
    group_data = await _ensure_group_toggle_async(bot, group_id)

    changed = _set_feature_states(group_data, target_names, False)
    if changed:
        _write_json_file(_group_toggle_path(group_id), group_data)
        await disable_cmd.finish(f"已在本群禁用功能『{display_name}』✓")

    await disable_cmd.finish(f"功能『{display_name}』在本群已经是禁用状态")
