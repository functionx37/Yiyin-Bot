from __future__ import annotations

import asyncio
import csv
import random
import time
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import jieba
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import Rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAYU_CSV_PATH = PROJECT_ROOT / "assets" / "documents" / "vayu.csv"

CHUNK_INTERVAL_SECONDS = 10
READ_ANSWER_DELAY_SECONDS = 30
AUTO_REVEAL_SECONDS = 120
MAX_CHUNKS = 5
PUNCT_BIAS = 1
ANSWER_PREFIX = "%"
SESSION_ROUNDS = 5
CORRECT_SCORE = 2

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
    total_rounds: int = SESSION_ROUNDS
    current_round: int = 1
    scores: dict[int, int] = field(default_factory=dict)
    display_names: dict[int, str] = field(default_factory=dict)
    participant_order: dict[int, int] = field(default_factory=dict)
    used_record_ids: set[int] = field(default_factory=set)
    previous_answers: frozenset[str] = field(default_factory=frozenset)
    accepting_answers: bool = False
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


def _parse_session_rounds(args_text: str) -> tuple[int | None, str | None]:
    if not args_text:
        return SESSION_ROUNDS, None

    parts = args_text.split()
    if len(parts) != 1:
        return None, "用法：/随蓝 [n]"

    try:
        rounds = int(parts[0])
    except ValueError:
        return None, "参数 n 必须是整数，用法：/随蓝 [n]"

    if rounds <= 0:
        return None, "参数 n 必须大于 0，用法：/随蓝 [n]"

    return rounds, None


def _sanitize_display_name(name: str) -> str:
    cleaned = name.replace("\r", " ").replace("\n", " ").strip()
    return cleaned or "群友"


def _display_name(event: GroupMessageEvent) -> str:
    sender = event.sender
    if not sender:
        return str(event.user_id)

    card = _sanitize_display_name(sender.card or "")
    if card != "群友":
        return card

    nickname = _sanitize_display_name(sender.nickname or "")
    if nickname != "群友":
        return nickname

    return str(event.user_id)


def _parse_answers(answer_field: str) -> list[str]:
    return answer_field.split("/")


def _normalize_answer_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


@lru_cache(maxsize=4096)
def _accepted_answers(answer_field: str) -> frozenset[str]:
    return frozenset(
        normalized
        for raw_answer in _parse_answers(answer_field)
        if (normalized := _normalize_answer_text(raw_answer))
    )


def _judge_answer(input_text: str, answer_field: str) -> bool:
    normalized_input = _normalize_answer_text(input_text)
    if not normalized_input:
        return False
    return normalized_input in _accepted_answers(answer_field)


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


def _draw_question(exclude_ids: set[int] | None = None) -> VayuRecord | None:
    if not VAYU_RECORDS:
        return None
    if exclude_ids:
        candidates = [record for record in VAYU_RECORDS if record.id not in exclude_ids]
        if candidates:
            return random.choice(candidates)
    return random.choice(VAYU_RECORDS)


def _build_chunks(record: VayuRecord) -> list[str]:
    words = _tokenize_description(record.desc)
    return _merge_chunks(words, MAX_CHUNKS)


def _format_score_delta(score: int) -> str:
    return f"+{score}" if score > 0 else str(score)


def _question_surface(record: VayuRecord) -> str:
    return f"{record.vayu}{record.desc}".strip()


def _build_first_chunk(record: VayuRecord, chunks: list[str]) -> str:
    first_chunk = f"{record.vayu}{chunks[0]}"
    if len(chunks) == 1:
        return f"{first_chunk}\n我读完了。"
    return first_chunk


def _remember_participant(game: VayuGame, user_id: int, name: str) -> None:
    if user_id not in game.participant_order:
        game.participant_order[user_id] = len(game.participant_order)
    game.display_names[user_id] = _sanitize_display_name(name)


def _change_score(game: VayuGame, user_id: int, name: str, delta: int) -> None:
    _remember_participant(game, user_id, name)
    game.scores[user_id] = game.scores.get(user_id, 0) + delta


def _is_previous_round_answer(game: VayuGame, answer: str) -> bool:
    return answer in game.previous_answers


def _build_round_result_message(record: VayuRecord, winner_id: int | None = None):
    summary = f"题面：{_question_surface(record)}\n答案：{record.answer}"
    if winner_id is None:
        return summary
    return MessageSegment.at(winner_id) + MessageSegment.text(
        f"✔️回答正确！{_format_score_delta(CORRECT_SCORE)}分\n{summary}"
    )


def _build_wrong_answer_message(user_id: int):
    return MessageSegment.at(user_id) + MessageSegment.text("✖️回答错误！")


def _build_scoreboard_message(game: VayuGame) -> str:
    if not game.scores:
        return "本局随蓝成绩：\n暂无成绩"

    ranking = sorted(
        game.scores.items(),
        key=lambda item: (-item[1], game.participant_order[item[0]], item[0]),
    )

    lines = ["本局随蓝成绩："]
    prev_score: int | None = None
    current_rank = 0
    for idx, (user_id, score) in enumerate(ranking, start=1):
        if score != prev_score:
            current_rank = idx
            prev_score = score
        nickname = game.display_names.get(user_id, str(user_id))
        lines.append(f"{current_rank}.{nickname} {score}分")
    return "\n".join(lines)


def _set_round(game: VayuGame, record: VayuRecord, round_number: int) -> None:
    game.record = record
    game.chunks = _build_chunks(record)
    game.current_round = round_number
    game.used_record_ids.add(record.id)
    game.accepting_answers = False
    game.read_complete_at = None
    game.broadcaster_task = None
    game.auto_reveal_task = None


def _cancel_round_tasks(game: VayuGame) -> None:
    current = asyncio.current_task()
    for task in (game.broadcaster_task, game.auto_reveal_task):
        if task is not None and task is not current and not task.done():
            task.cancel()
    game.broadcaster_task = None
    game.auto_reveal_task = None
    game.accepting_answers = False
    game.read_complete_at = None


def _remove_game(group_id: int, game: VayuGame) -> None:
    if _GROUP_GAMES.get(group_id) is game:
        _GROUP_GAMES.pop(group_id, None)
    _cancel_round_tasks(game)


def _settle_round_locked(
    game: VayuGame,
    *,
    winner_id: int | None = None,
    winner_name: str | None = None,
) -> tuple[object, bool, str | None]:
    record = game.record
    game.previous_answers = _accepted_answers(record.answer)
    _cancel_round_tasks(game)

    if winner_id is not None:
        _change_score(
            game,
            user_id=winner_id,
            name=winner_name or str(winner_id),
            delta=CORRECT_SCORE,
        )

    result_message = _build_round_result_message(record, winner_id=winner_id)
    if game.current_round >= game.total_rounds:
        return result_message, False, _build_scoreboard_message(game)

    next_record = _draw_question(game.used_record_ids)
    if next_record is None:
        return result_message, False, _build_scoreboard_message(game)

    _set_round(game, next_record, game.current_round + 1)
    return result_message, True, None


async def _start_round(bot: Bot, game: VayuGame) -> bool:
    try:
        await bot.send_group_msg(
            group_id=game.group_id,
            message=_build_first_chunk(game.record, game.chunks),
        )
    except Exception:
        logger.exception("发送随蓝首段失败")
        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is game:
                _remove_game(game.group_id, game)
        return False

    async with _group_lock(game.group_id):
        current = _GROUP_GAMES.get(game.group_id)
        if current is not game:
            return False
        game.accepting_answers = True
        if len(game.chunks) == 1:
            game.read_complete_at = time.monotonic()
            game.auto_reveal_task = asyncio.create_task(_start_auto_reveal(bot, game))
        else:
            game.broadcaster_task = asyncio.create_task(_broadcast_remaining_chunks(bot, game))
    return True


async def _dispatch_round_settlement(
    bot: Bot,
    game: VayuGame,
    result_message: object,
    *,
    should_start_next_round: bool,
    final_scoreboard: str | None,
) -> None:
    await bot.send_group_msg(group_id=game.group_id, message=result_message)
    if should_start_next_round:
        await _start_round(bot, game)
        return
    if final_scoreboard is not None:
        await bot.send_group_msg(group_id=game.group_id, message=final_scoreboard)


async def _start_auto_reveal(bot: Bot, game: VayuGame) -> None:
    try:
        await asyncio.sleep(AUTO_REVEAL_SECONDS)
        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is not game or not game.accepting_answers:
                return
            result_message, should_start_next_round, final_scoreboard = _settle_round_locked(
                game
            )
            if not should_start_next_round:
                _remove_game(game.group_id, game)
        await _dispatch_round_settlement(
            bot,
            game,
            result_message,
            should_start_next_round=should_start_next_round,
            final_scoreboard=final_scoreboard,
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
                if current is not game or not game.accepting_answers:
                    return
            body = game.chunks[idx]
            if idx == len(game.chunks) - 1:
                body = f"{body}\n我读完了。"
            await bot.send_group_msg(group_id=game.group_id, message=body)

        async with _group_lock(game.group_id):
            current = _GROUP_GAMES.get(game.group_id)
            if current is not game or not game.accepting_answers:
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
                _remove_game(game.group_id, game)


def _answer_rule(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.self_id == event.user_id:
        return False
    return _normalize_answer_text(event.get_plaintext()).startswith(ANSWER_PREFIX)


vayu_cmd = on_command("随蓝", priority=10, block=True)
answer_matcher = on_message(rule=Rule(_answer_rule), priority=10, block=True)
reveal_cmd = on_command("看答案", priority=10, block=True)


@vayu_cmd.handle()
async def handle_vayu(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        return

    total_rounds, error = _parse_session_rounds(args.extract_plain_text().strip())
    if error:
        await vayu_cmd.finish(error)

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
            total_rounds=total_rounds,
            used_record_ids={record.id},
        )
        _GROUP_GAMES[event.group_id] = game

    await _start_round(bot, game)


@answer_matcher.handle()
async def handle_answer(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return

    text = _normalize_answer_text(event.get_plaintext())
    answer = text.removeprefix(ANSWER_PREFIX).strip()
    if not answer:
        return

    correct = False
    ignored_previous_answer = False
    result_message: object | None = None
    should_start_next_round = False
    final_scoreboard: str | None = None

    async with _group_lock(event.group_id):
        game = _GROUP_GAMES.get(event.group_id)
        if game is None or not game.accepting_answers:
            return
        correct = _judge_answer(answer, game.record.answer)
        if correct:
            result_message, should_start_next_round, final_scoreboard = _settle_round_locked(
                game,
                winner_id=event.user_id,
                winner_name=_display_name(event),
            )
            if not should_start_next_round:
                _remove_game(event.group_id, game)
        elif _is_previous_round_answer(game, answer):
            ignored_previous_answer = True
        else:
            _remember_participant(game, event.user_id, _display_name(event))
            game.scores.setdefault(event.user_id, 0)

    if correct:
        await _dispatch_round_settlement(
            bot,
            game,
            result_message,
            should_start_next_round=should_start_next_round,
            final_scoreboard=final_scoreboard,
        )
        return

    if ignored_previous_answer:
        return

    await bot.send_group_msg(
        group_id=event.group_id,
        message=_build_wrong_answer_message(event.user_id),
    )


@reveal_cmd.handle()
async def handle_reveal(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return

    result_message: object | None = None
    should_start_next_round = False
    final_scoreboard: str | None = None

    async with _group_lock(event.group_id):
        game = _GROUP_GAMES.get(event.group_id)
        if game is None or not game.accepting_answers:
            return
        if game.read_complete_at is None:
            await reveal_cmd.finish("题目念完三十秒后才能看答案")
        if time.monotonic() - game.read_complete_at < READ_ANSWER_DELAY_SECONDS:
            await reveal_cmd.finish("题目念完三十秒后才能看答案")
        result_message, should_start_next_round, final_scoreboard = _settle_round_locked(
            game
        )
        if not should_start_next_round:
            _remove_game(event.group_id, game)

    await _dispatch_round_settlement(
        bot,
        game,
        result_message,
        should_start_next_round=should_start_next_round,
        final_scoreboard=final_scoreboard,
    )
