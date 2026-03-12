"""
NoneBot2 随机选择插件
- 命令：/选 <选项1>还是<选项2>[还是<选项3>...]
- 选项可用『还是』、空格、/ 分隔
- 功能：从给定的多个选项中随机选择一个；特判规则见 config/choose_special.jsonl（一行一条）

规则格式：每条 rule 为 condition + answer。condition 可为：
- 字符串：选项里出现该词即成立
- 数组：这些选项全部出现才成立（与）
- {"and": [条件, ...]}：所有子条件都成立
- {"or": [条件, ...]}：至少一个子条件成立
可嵌套，如 {"or": [{"and": ["A","B"]}, {"and": ["A","C"]}]}。
"""

import json
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

choose_cmd = on_command("选", priority=10, block=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SPECIAL_CONFIG_PATH = PROJECT_ROOT / "config" / "choose_special.jsonl"


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
    """加载特判规则（JSONL：一行一条 rule）。每次调用都读文件，保证热更新。"""
    if not SPECIAL_CONFIG_PATH.exists():
        return []
    rules = []
    try:
        with open(SPECIAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rules.append(json.loads(line))
    except Exception:
        pass
    return rules


def _eval_condition(cond: str | list | dict, opt_set: set[str]) -> bool:
    """
    求值条件：与/或嵌套。
    - 字符串：选项里包含该词即成立
    - 数组：这些选项全部在选项里才成立（与）
    - {"and": [条件, ...]} / {"or": [条件, ...]}：与 / 或
    """
    if isinstance(cond, str):
        return cond in opt_set
    if isinstance(cond, list):
        return bool(cond) and set(cond) <= opt_set
    if isinstance(cond, dict):
        if "and" in cond:
            return all(_eval_condition(c, opt_set) for c in cond["and"])
        if "or" in cond:
            return any(_eval_condition(c, opt_set) for c in cond["or"])
    return False


def _apply_special(options: list[str]) -> str | None:
    """若当前选项匹配某条特判规则，返回必选答案；否则返回 None。"""
    opt_set = set(options)
    for rule in _load_special_rules():
        answer = rule.get("answer")
        cond = rule.get("condition")
        if not answer or cond is None:
            continue
        if _eval_condition(cond, opt_set):
            return answer
    return None


@choose_cmd.handle()
async def handle_choose(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await choose_cmd.finish(
            "用法：/选 <选项1>还是<选项2>[还是<选项3>...]\n"
            "选项可用『还是』、空格、/ 分隔\n"
            "示例：/选 火锅还是烧烤还是麻辣烫  或  /选 甜豆花/咸豆花"
        )

    options = _parse_options(raw)

    if len(options) < 2:
        await choose_cmd.finish(
            "至少需要两个选项哦，用『还是』、空格或 / 分隔\n"
            "示例：/选 火锅还是烧烤  或  /选 甜豆花 咸豆花"
        )

    forced = _apply_special(options)
    if forced is not None:
        await choose_cmd.finish(f"那必须选{forced}啊")
    chosen = random.choice(options)
    await choose_cmd.finish(f"我建议你选择：{chosen}")
