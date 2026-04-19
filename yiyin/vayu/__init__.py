from __future__ import annotations

import asyncio
import csv
import random
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import jieba
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.rule import Rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAYU_CSV_PATH = PROJECT_ROOT / "assets" / "documents" / "vayu.csv"

CHUNK_INTERVAL_SECONDS = 5
READ_ANSWER_DELAY_SECONDS = 30
AUTO_REVEAL_SECONDS = 120
MAX_CHUNKS = 7
PUNCT_BIAS = 1

BAD_END_CATEGORIES = {"Ps", "Pi"}
BAD_START_CATEGORIES = {"Pe", "Pf", "Po"}


@dataclass(slots=True)
class VayuRecord:
    id: int
    vayu: str
    source: str
    answer: str
    desc: str


@dataclass(slots=True)
class VayuGame:
    group_id: int
    record: VayuRecord
    chunks: list[str]
    read_complete_at: float | None = None
    broadcaster_task: asyncio.Task | None = None
    auto_reveal_task: asyncio.Task | None = None


def _load_records() -> list[VayuRecord]:
    if not VAYU_CSV_PATH.exists():
        logger.warning("随蓝题库不存在: {}", VAYU_CSV_PATH)
        return []

    records: list[VayuRecord] = []
    try:
        with open(VAYU_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                try:
                    records.append(
                        VayuRecord(
                            id=int(row["id"]),
                            vayu=(row.get("vayu") or "").strip(),
                            source=(row.get("source") or "").strip(),
                            answer=(row.get("answer") or "").strip(),
                            desc=row.get("desc") or "",
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    logger.warning("跳过无效随蓝题目: {}", row)
    except OSError:
        logger.exception("读取随蓝题库失败")
        return []

    return records


VAYU_RECORDS = _load_records()
_GROUP_GAMES: dict[int, VayuGame] = {}
_GROUP_LOCKS: dict[int, asyncio.Lock] = {}


def _group_lock(group_id: int) -> asyncio.Lock:
    lock = _GROUP_LOCKS.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _GROUP_LOCKS[group_id] = lock
    return lock


def _parse_answers(answer_field: str) -> list[str]:
    return answer_field.split("/")


def _judge_answer(input_text: str, answer_field: str) -> bool:
    return input_text in _parse_answers(answer_field)


def _tokenize_description(desc: str) -> list[str]:
    description = desc.strip()
    if not description:
        return [""]
    if description.startswith("1."):
        return [f"{word} " for word in description.split()]
    return [word for word in jieba.lcut(description) if word]


def _category_of(char: str) -> str:
    if not char:
        return ""
    return unicodedata.category(char)


def _can_split_at(words: list[str], idx: int) -> bool:
    prev_last = words[idx - 1][-1:]
    next_first = words[idx][:1]
    return (
        _category_of(prev_last) not in BAD_END_CATEGORIES
        and _category_of(next_first) not in BAD_START_CATEGORIES
    )


def _split_bonus(words: list[str], idx: int) -> float:
    if idx <= 0:
        return 1.0
    prev_last = words[idx - 1][-1:]
    return PUNCT_BIAS if _category_of(prev_last) in BAD_START_CATEGORIES else 1.0


def _run_chunk_dp(words: list[str], chunk_count: int, *, strict: bool) -> list[str] | None:
    n = len(words)
    prefix_len = [0] * (n + 1)
    for i in range(1, n + 1):
        prefix_len[i] = prefix_len[i - 1] + len(words[i - 1])

    total_len = prefix_len[n]
    inf = float("inf")
    dp = [[inf] * (chunk_count + 1) for _ in range(n + 1)]
    choice = [[-1] * (chunk_count + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(1, n + 1):
        max_k = min(i, chunk_count)
        for k in range(1, max_k + 1):
            for j in range(k - 1, i):
                if dp[j][k - 1] == inf:
                    continue
                if strict and j > 0 and not _can_split_at(words, j):
                    continue
                seg_len = prefix_len[i] - prefix_len[j]
                remaining_len = total_len - prefix_len[j]
                remaining_chunks = chunk_count - k + 1
                dynamic_target = remaining_len / remaining_chunks
                cost = abs(seg_len - dynamic_target) * _split_bonus(words, j)
                candidate = dp[j][k - 1] + cost
                if candidate < dp[i][k]:
                    dp[i][k] = candidate
                    choice[i][k] = j

    if choice[n][chunk_count] < 0:
        return None

    chunks: list[str] = []
    cur = n
    k = chunk_count
    while k > 0:
        j = choice[cur][k]
        if j < 0:
            return None
        chunks.append("".join(words[j:cur]))
        cur = j
        k -= 1
    chunks.reverse()
    return chunks


def _merge_chunks(words: list[str], max_chunks: int) -> list[str]:
    if not words:
        return [""]
    if max_chunks <= 1:
        return ["".join(words)]
    if len(words) <= max_chunks:
        return words[:]

    chunks = _run_chunk_dp(words, max_chunks, strict=True)
    if chunks is None:
        chunks = _run_chunk_dp(words, max_chunks, strict=False)
    if chunks is None:
        return ["".join(words)]
    return chunks


def _draw_question() -> VayuRecord | None:
    if not VAYU_RECORDS:
        return None
    return random.choice(VAYU_RECORDS)


def _build_chunks(record: VayuRecord) -> list[str]:
    words = _tokenize_description(record.desc)
    return _merge_chunks(words, MAX_CHUNKS)


async def _remove_game(group_id: int, game: VayuGame) -> None:
    current = asyncio.current_task()
    if _GROUP_GAMES.get(group_id) is game:
        _GROUP_GAMES.pop(group_id, None)

    for task in (game.broadcaster_task, game.auto_reveal_task):
        if task is not None and task is not current and not task.done():
            task.cancel()


async def _start_auto_reveal(bot: Bot, game: VayuGame) -> None:
    try:
        await asyncio.sleep(AUTO_REVEAL_SECONDS)
        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is not game:
                return
            await _remove_game(game.group_id, game)
        await bot.send_group_msg(
            group_id=game.group_id,
            message=f"答案是：{game.record.answer}",
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("随蓝自动公布答案失败")


async def _broadcast_remaining_chunks(bot: Bot, game: VayuGame) -> None:
    try:
        for idx in range(1, len(game.chunks)):
            await asyncio.sleep(CHUNK_INTERVAL_SECONDS)
            async with _group_lock(game.group_id):
                current = _GROUP_GAMES.get(game.group_id)
                if current is not game:
                    return
            body = game.chunks[idx]
            if idx == len(game.chunks) - 1:
                body = f"{body}\n我读完了。"
            await bot.send_group_msg(group_id=game.group_id, message=body)

        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is not game:
                return
            game.read_complete_at = time.monotonic()
            game.auto_reveal_task = asyncio.create_task(_start_auto_reveal(bot, game))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("随蓝分段播报失败")
        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is game:
                await _remove_game(game.group_id, game)


def _answer_rule(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.self_id == event.user_id:
        return False
    return event.get_plaintext().lstrip().startswith("%")


vayu_cmd = on_command("随蓝", priority=10, block=True)
answer_matcher = on_message(rule=Rule(_answer_rule), priority=10, block=True)
reveal_cmd = on_command("看答案", priority=10, block=True)


@vayu_cmd.handle()
async def handle_vayu(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return

    async with _group_lock(event.group_id):
        if event.group_id in _GROUP_GAMES:
            return

        record = _draw_question()
        if record is None:
            await vayu_cmd.finish("未找到符合条件的随蓝")

        game = VayuGame(
            group_id=event.group_id,
            record=record,
            chunks=_build_chunks(record),
        )
        _GROUP_GAMES[event.group_id] = game

    first_chunk = f"{record.vayu}{game.chunks[0]}"
    if len(game.chunks) == 1:
        first_chunk = f"{first_chunk}\n我读完了。"

    try:
        await vayu_cmd.send(first_chunk)
    except Exception:
        logger.exception("发送随蓝首段失败")
        async with _group_lock(event.group_id):
            current = _GROUP_GAMES.get(event.group_id)
            if current is game:
                await _remove_game(event.group_id, game)
        return

    async with _group_lock(event.group_id):
        current = _GROUP_GAMES.get(event.group_id)
        if current is not game:
            return
        if len(game.chunks) == 1:
            game.read_complete_at = time.monotonic()
            game.auto_reveal_task = asyncio.create_task(_start_auto_reveal(bot, game))
        else:
            game.broadcaster_task = asyncio.create_task(_broadcast_remaining_chunks(bot, game))


@answer_matcher.handle()
async def handle_answer(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return

    text = event.get_plaintext().lstrip()
    answer = text[1:]
    if not answer:
        return

    async with _group_lock(event.group_id):
        game = _GROUP_GAMES.get(event.group_id)
        if game is None:
            return
        correct = _judge_answer(answer, game.record.answer)
        if correct:
            await _remove_game(event.group_id, game)

    at_msg = MessageSegment.at(event.user_id)
    if correct:
        await answer_matcher.finish(at_msg + MessageSegment.text(" ✔️回答正确！"))
    await answer_matcher.finish(at_msg + MessageSegment.text(" ✖️回答错误！"))


@reveal_cmd.handle()
async def handle_reveal(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return

    async with _group_lock(event.group_id):
        game = _GROUP_GAMES.get(event.group_id)
        if game is None:
            return
        if game.read_complete_at is None:
            await reveal_cmd.finish("题目念完三十秒后才能看答案")
        if time.monotonic() - game.read_complete_at < READ_ANSWER_DELAY_SECONDS:
            await reveal_cmd.finish("题目念完三十秒后才能看答案")
        answer = game.record.answer
        await _remove_game(event.group_id, game)

    await reveal_cmd.finish(f"答案是：{answer}")
