"""
拼词子功能：dqxm
- 指令：/dqxm (%模式1 %模式2...) <文本>，或省略文本改为引用一条消息
- 模式：与 /拼 相同的声母序列；模式以 # 结尾（如 %ccb#）表示与模式库取并集；
  不指定模式时使用模式库（assets/documents/dqxm.json）中的全部模式
- 辅助指令：/新增dqxm <模式>（向模式库添加一行）、/查看dqxm（一条消息列出全部模式）
- 流程：把模式和聊天内容交给 DeepSeek（deepseek-flash）现场生成贴合语境的
  中英混搭词组，本地代码再按声母规则严格清洗，不符合的一律丢弃
- 默认关闭，需群内 /启用 dqxm
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.log import logger
from nonebot.params import CommandArg

try:
    from pypinyin import Style, pinyin as _pinyin_all
except ImportError:
    # 部署环境未同步依赖时降级为仅用本地拼音表判断，保证 /拼 不受影响
    _pinyin_all = None

from yiyin.word_spelling import PINYIN_DIR, _parse_pinyin_pattern

# ==================== 配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DQXM_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "dqxm.json"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_TIMEOUT = 90

MAX_TEXT_LENGTH = 1200  # 送入 LLM 的聊天内容截断长度
MAX_WORDS = 5  # 最多展示的词组数
MAX_TOKENS = 800

# 汉字（基本区+扩展A+兼容区）；词组仅允许汉字与英文字母
_WORD_CHARS_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaffA-Za-z]+")

SYSTEM_PROMPT = """你在参与QQ群里的「声母拼词」文字游戏。主持人给你一段聊天内容和一组可选的声母模式，你要现场创作贴合语境的词组。

【规则】
1. 模式是拼音声母串，zh、ch、sh 是完整声母；模式里的 / 表示该位置任选其一，' 只是分隔符。
2. 词组的单位数必须与模式的声母数完全一致：第 i 个单位对应第 i 个声母。
3. 单位只能是一个汉字或一个英文单词：
   - 汉字贡献其拼音声母，如 盯→d、吃→ch、安→a、五→w；
   - 英文单词贡献首字母，如 money→m、bug→b。
4. c/ch、z/zh、s/sh 通用：神(sh)可以顶 s 位，戳(ch)可以顶 c 位，反之亦然。
5. 中英混搭欢迎：某个位置换成英文单词更自然、更有梗时，大胆混搭。
6. 只用常见汉字和常见英文单词，不用生僻字、不造专有缩写。

【创作方法】先读懂聊天内容的人物、事件、情绪和梗，再对候选模式做多角度联想：字面概括、内心吐槽、网络热梗、谐音双关、夸张总结；挑读起来顺口、有画面感、能让人会心一笑的组合，几个词组之间尽量换角度、换模式。

【参考示例】注意单位与声母的对应关系
- 模式 dbq → 对不起（d·b·q）
- 模式 yyds → 永远滴神（y·y·d·sh，sh 顶 s）
- 模式 yde → 有点emo（y·d·e，英文顶汉字）
- 聊天内容「改了一晚上代码还是报错，天都亮了」＋模式 ccb → 戳穿bug（ch·ch·b，ch 顶 c）

【输出格式】严格遵守
- 每行一个词组，格式：模式|词组
- 共 5 行；模式可以复用，也可以偏爱某几个
- 不要序号、不要解释、不要任何多余文字"""

# ==================== 模式库读写 ====================
def _load_pattern_library() -> list[str]:
    """读取 dqxm.json 中的声母模式，格式异常时按空库处理。"""
    if not DQXM_JSON_PATH.exists():
        return []
    try:
        data = json.loads(DQXM_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("dqxm: 读取模式库失败: {}", e)
        return []
    if not isinstance(data, list):
        return []
    return [item.strip().lower() for item in data if isinstance(item, str) and item.strip()]


def _save_pattern_library(patterns: list[str]) -> bool:
    try:
        DQXM_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        DQXM_JSON_PATH.write_text(
            json.dumps(patterns, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError as e:
        logger.warning("dqxm: 保存模式库失败: {}", e)
        return False


# ==================== 声母规则判断 ====================
_CHAR_TABLE_KEYS: dict[str, set[str]] | None = None


def _load_char_table_keys() -> dict[str, set[str]]:
    """加载 assets/documents/pinyin/ 字库，得到 汉字 -> 声母键集合（懒加载，只读一次）。"""
    global _CHAR_TABLE_KEYS
    if _CHAR_TABLE_KEYS is not None:
        return _CHAR_TABLE_KEYS

    mapping: dict[str, set[str]] = {}
    for path in sorted(PINYIN_DIR.glob("*.json")):
        try:
            chars = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("dqxm: 读取拼音字库失败 {}: {}", path, e)
            continue
        if not isinstance(chars, list):
            continue
        for ch in chars:
            if isinstance(ch, str) and ch:
                mapping.setdefault(ch, set()).add(path.stem)

    _CHAR_TABLE_KEYS = mapping
    return mapping


@lru_cache(maxsize=None)
def _char_initial_letters(ch: str) -> frozenset[str]:
    """汉字所有可能的首字母：pypinyin 全部读法（多音字取并集）∪ 本地拼音表。

    统一取首字母后，zh/ch/sh 自然折叠为 z/c/s，等效于 c/ch、z/zh、s/sh 宽松互认。
    无法解析读音的汉字（过于生僻）返回空集合。
    """
    letters: set[str] = set()
    if _pinyin_all is not None:
        for readings in _pinyin_all(
            ch, style=Style.NORMAL, heteronym=True, errors="default"
        ):
            for reading in readings:
                if reading and reading[0].isascii() and reading[0].isalpha():
                    letters.add(reading[0].lower())
    letters.update(key[0] for key in _load_char_table_keys().get(ch, ()))
    return frozenset(letters)


def _slot_initial_letters(slot: str | list[str]) -> set[str]:
    """把一个模式位置（声母或 / 分隔的备选列表）折成首字母集合。"""
    keys = slot if isinstance(slot, list) else [slot]
    return {key[0] for key in keys}


def _split_units(word: str) -> list[tuple[bool, str]] | None:
    """把词组拆成 (是否汉字, 单位) 序列：每个汉字是一个单位，连续英文是一个单位。

    含汉字和英文字母以外的字符（数字、标点、空格等）时返回 None。
    """
    if not _WORD_CHARS_RE.fullmatch(word):
        return None

    units: list[tuple[bool, str]] = []
    latin = ""
    for ch in word:
        if ch.isascii() and ch.isalpha():
            latin += ch
            continue
        if latin:
            units.append((False, latin))
            latin = ""
        units.append((True, ch))
    if latin:
        units.append((False, latin))
    return units


def _match_pattern(
    word: str,
    patterns: list[tuple[str, list[str | list[str]]]],
) -> str | None:
    """返回词组命中的第一个模式文本；单位数不符或声母不符返回 None。"""
    units = _split_units(word)
    if not units:
        return None

    for pattern_text, slots in patterns:
        if len(units) != len(slots):
            continue
        for (is_han, unit), slot in zip(units, slots):
            letters = _slot_initial_letters(slot)
            if is_han:
                if not (_char_initial_letters(unit) & letters):
                    break
            elif unit[0].lower() not in letters:
                break
        else:
            return pattern_text
    return None


# ==================== 指令参数解析 ====================
def _split_mode_args(args_text: str) -> tuple[list[str], bool, str]:
    """从指令参数中拆出 %模式 串与剩余文本。

    模式可以空格分隔（%ccb %dqxm）也可以连写（%ccb%dqxm）；
    模式以 # 结尾（或单独一个 # 参数）表示与模式库取并集。
    """
    modes: list[str] = []
    union_with_library = False
    rest = args_text
    while True:
        stripped = rest.lstrip()
        if not stripped:
            break
        if not stripped.startswith("%"):
            if stripped.split(None, 1)[0] == "#":
                union_with_library = True
                rest = stripped[1:]
                continue
            break

        index = 0
        while index < len(stripped) and not stripped[index].isspace():
            index += 1
        token = stripped[:index]
        rest = stripped[index:]
        for part in token.split("%"):
            if not part:
                continue
            if part.endswith("#"):
                part = part[:-1]
                union_with_library = True
            if part:
                modes.append(part.lower())
    return modes, union_with_library, rest.strip()


def _build_patterns(
    user_modes: list[str], union_with_library: bool
) -> tuple[list[tuple[str, list[str | list[str]]]], str | None]:
    """确定本次使用的模式：用户指定的（严格校验）或模式库全部（宽松跳过无效项）。"""
    library = _load_pattern_library()
    if not user_modes:
        candidate_texts = list(library)
        strict = False
    elif union_with_library:
        candidate_texts = user_modes + [m for m in library if m not in user_modes]
        strict = True
    else:
        candidate_texts = list(user_modes)
        strict = True

    if not candidate_texts:
        return [], "当前没有可用模式：用 /新增dqxm <模式> 添加，或在指令里用 %模式 指定"

    patterns: list[tuple[str, list[str | list[str]]]] = []
    for mode_text in candidate_texts:
        slots, error = _parse_pinyin_pattern(mode_text)
        if error is not None:
            if strict:
                return [], error
            logger.warning("dqxm: 跳过模式库中的无效模式 {}", mode_text)
            continue
        patterns.append((mode_text, slots))
    if not patterns:
        return [], "没有可用的有效模式"
    return patterns, None


async def _extract_reply_text(bot: Bot, event: GroupMessageEvent) -> str:
    """取引用消息的纯文本；event.reply 缺字段时回退 get_msg。"""
    if event.reply is None:
        return ""
    if event.reply.message:
        text = event.reply.message.extract_plain_text().strip()
        if text:
            return text
    try:
        msg_data = await bot.get_msg(message_id=event.reply.message_id)
        raw_msg = msg_data.get("message", "")
        if isinstance(raw_msg, Message):
            return raw_msg.extract_plain_text().strip()
        if isinstance(raw_msg, (list, str)):
            return Message(raw_msg).extract_plain_text().strip()
    except Exception as e:
        logger.warning("dqxm: 获取引用消息失败: {}: {}", type(e).__name__, e)
    return ""


# ==================== DeepSeek 调用 ====================
async def _deepseek_chat(system: str, user: str) -> str | None:
    """调用 DeepSeek Chat Completions，返回回复文本，失败返回 None。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        logger.warning("dqxm: 未配置 DEEPSEEK_API_KEY")
        return None

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 1.3,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=DEEPSEEK_TIMEOUT) as client:
            resp = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions", json=payload, headers=headers
            )
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        logger.warning("dqxm: DeepSeek 请求失败: {}: {}", type(e).__name__, e)
        return None

    if resp.status_code != 200:
        logger.warning(
            "dqxm: DeepSeek 非 200: status={} body={}", resp.status_code, resp.text[:300]
        )
        return None

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("dqxm: DeepSeek 响应解析失败: {}", resp.text[:300])
        return None
    return content if isinstance(content, str) else None


def _build_user_prompt(
    text: str, patterns: list[tuple[str, list[str | list[str]]]]
) -> str:
    patterns_block = "\n".join(pattern_text for pattern_text, _ in patterns)
    return f"聊天内容：\n{text}\n\n可选模式：\n{patterns_block}"


# ==================== 输出清洗 ====================
def _extract_candidate_words(llm_text: str) -> list[str]:
    """从模型返回中逐行提取词组：去掉序号/列表符/代码围栏，按 | 或 ： 取词组部分。"""
    words: list[str] = []
    for raw_line in llm_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        line = line.strip("`").strip()
        line = re.sub(r"^(?:\d+[.、)]\s*|[-*•·>]+\s*)", "", line)
        if "|" in line or "｜" in line or ":" in line or "：" in line:
            line = re.split(r"[|｜:：]", line)[-1]
        line = line.strip().strip("\"'「」『』 ").strip()
        if line:
            words.append(line)
    return words


def _clean_words(
    llm_text: str, patterns: list[tuple[str, list[str | list[str]]]]
) -> list[tuple[str, str]]:
    """按声母规则清洗模型输出，返回 (模式, 词组) 列表，去重后最多 MAX_WORDS 个。"""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for word in _extract_candidate_words(llm_text):
        matched = _match_pattern(word, patterns)
        if matched is None:
            continue
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        results.append((matched, word))
        if len(results) >= MAX_WORDS:
            break
    return results


# ==================== 注册命令 ====================
dqxm_matcher = on_command("dqxm", priority=10, block=True)
add_dqxm_matcher = on_command("新增dqxm", priority=10, block=True)
list_dqxm_matcher = on_command("查看dqxm", priority=10, block=True)


@dqxm_matcher.handle()
async def _handle_dqxm(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
):
    user_modes, union_with_library, text = _split_mode_args(
        args.extract_plain_text().strip()
    )
    if not text:
        text = await _extract_reply_text(bot, event)
    if not text:
        await dqxm_matcher.finish(
            "用法：/dqxm [%模式1 %模式2…] <文本>\n"
            "也可以引用一条消息代替文本，示例：/dqxm %ccb 今天吃什么\n"
            "%模式 以 # 结尾表示与模式库取并集（如 %ccb#），不指定模式时使用全部模式库"
        )

    patterns, error = _build_patterns(user_modes, union_with_library)
    if error:
        await dqxm_matcher.finish(error)

    text = text[:MAX_TEXT_LENGTH]
    llm_text = await _deepseek_chat(
        SYSTEM_PROMPT, _build_user_prompt(text, patterns)
    )
    if llm_text is None:
        await dqxm_matcher.finish("生成失败：DeepSeek 没有返回结果，稍后再试")

    logger.debug("dqxm: DeepSeek 原始返回: {}", llm_text)
    cleaned = _clean_words(llm_text, patterns)
    if not cleaned:
        await dqxm_matcher.finish("这轮没有生成出符合声母规则的词，换个文本再试一次")

    await dqxm_matcher.finish("\n".join(word for _, word in cleaned))


@add_dqxm_matcher.handle()
async def _handle_add_dqxm(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
):
    mode = args.extract_plain_text().strip().lower().strip("%").rstrip("#").strip()
    if not mode:
        await add_dqxm_matcher.finish("用法：/新增dqxm <模式>，例如 /新增dqxm ccb")

    _, error = _parse_pinyin_pattern(mode)
    if error:
        await add_dqxm_matcher.finish(error)

    library = _load_pattern_library()
    if mode in library:
        await add_dqxm_matcher.finish(f"模式 {mode} 已在模式库中")

    library.append(mode)
    if not _save_pattern_library(library):
        await add_dqxm_matcher.finish("保存模式库失败，请检查日志")
    await add_dqxm_matcher.finish(
        f"已添加模式 {mode}，当前共 {len(library)} 个，用 /查看dqxm 查看"
    )


@list_dqxm_matcher.handle()
async def _handle_list_dqxm(
    bot: Bot,
    event: GroupMessageEvent,
):
    library = _load_pattern_library()
    if not library:
        await list_dqxm_matcher.finish("dqxm 模式库为空，用 /新增dqxm <模式> 添加")
    await list_dqxm_matcher.finish(
        f"dqxm 模式库（{len(library)} 个）：\n" + "\n".join(library)
    )
