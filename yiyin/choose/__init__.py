"""
NoneBot2 随机选择插件
- 命令：/选 <选项1>还是<选项2>[还是<选项3>...]
- 选项可用「还是」、空格、/ 分隔
- 功能：从给定的多个选项中随机选择一个；特判规则见 config/choose_special.json
"""

import json
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

choose_cmd = on_command("选", priority=10, block=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SPECIAL_CONFIG_PATH = PROJECT_ROOT / "config" / "choose_special.json"

_SPECIAL_CACHE: list[dict] | None = None
_SPECIAL_MTIME: float = 0


def _parse_options(raw: str) -> list[str]:
    """用 还是、空格、/ 分隔选项，去重并保持顺序。"""
    s = raw.replace("/", " ")
    parts = s.split("还是")
    options = []
    for part in parts:
        for opt in part.split():
            opt = opt.strip()
            if opt:
                options.append(opt)
    return list(dict.fromkeys(options))


def _load_special_rules() -> list[dict]:
    """加载特判规则。"""
    global _SPECIAL_CACHE, _SPECIAL_MTIME
    if not SPECIAL_CONFIG_PATH.exists():
        return []
    mtime = SPECIAL_CONFIG_PATH.stat().st_mtime
    if _SPECIAL_CACHE is not None and mtime == _SPECIAL_MTIME:
        return _SPECIAL_CACHE
    try:
        with open(SPECIAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _SPECIAL_CACHE = data.get("rules", [])
        _SPECIAL_MTIME = mtime
        return _SPECIAL_CACHE
    except Exception:
        return []


def _apply_special(options: list[str]) -> str | None:
    """若当前选项匹配某条特判规则，返回必选答案；否则返回 None。"""
    opt_set = set(options)
    for rule in _load_special_rules():
        rule_opts = rule.get("options", [])
        answer = rule.get("answer")
        if rule_opts and answer and set(rule_opts) <= opt_set:
            return answer
    return None


@choose_cmd.handle()
async def handle_choose(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await choose_cmd.finish(
            "用法：/选 <选项1>还是<选项2>[还是<选项3>...]\n"
            "选项可用「还是」、空格、/ 分隔\n"
            "示例：/选 火锅还是烧烤还是麻辣烫  或  /选 甜豆花/咸豆花"
        )

    options = _parse_options(raw)

    if len(options) < 2:
        await choose_cmd.finish(
            "至少需要两个选项哦，用「还是」、空格或 / 分隔\n"
            "示例：/选 火锅还是烧烤  或  /选 甜豆花 咸豆花"
        )

    forced = _apply_special(options)
    if forced is not None:
        await choose_cmd.finish(f"那必须选{forced}啊")
    chosen = random.choice(options)
    await choose_cmd.finish(f"我建议你选择：{chosen}")
