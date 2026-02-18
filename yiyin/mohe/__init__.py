"""
NoneBot2 摩诃插件
- 命令：/随机摩诃    — 逐条发送 3-5 条随机摩诃语录
- 自动触发：每天不超过两次，在随机时间自动向已启用的群发送
- 默认关闭，需通过 /启用 摩诃 开启
"""

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path

from nonebot import get_bots, get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from yiyin.toggle import is_feature_enabled  # noqa: E402

# ==================== 资源路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MOHE_JSON_PATH = PROJECT_ROOT / "assets" / "documents" / "mohe.json"
MOHE_IMAGE_DIR = PROJECT_ROOT / "assets" / "images" / "mohe"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# ==================== 加载摩诃数据 ====================
with open(MOHE_JSON_PATH, "r", encoding="utf-8") as f:
    _raw_data: list[str] = json.load(f)

# 文本 + 图片统一池
MOHE_DATA: list[str | Path] = [s for s in _raw_data if s.strip()]

if MOHE_IMAGE_DIR.is_dir():
    for img in sorted(MOHE_IMAGE_DIR.iterdir()):
        if img.suffix.lower() in IMAGE_SUFFIXES:
            MOHE_DATA.append(img)


def _to_message(item: str | Path):
    """将数据项转为可发送的消息"""
    if isinstance(item, Path):
        return MessageSegment.image(item.read_bytes())
    return item


# ==================== 注册命令 ====================
random_mohe_cmd = on_command("随机摩诃", priority=10, block=True)


@random_mohe_cmd.handle()
async def handle_random_mohe(bot: Bot, event: GroupMessageEvent):
    """处理 /随机摩诃 命令：逐条发送 3-5 条随机摩诃语录"""
    group_id = str(event.group_id)

    if not is_feature_enabled("mohe", group_id):
        await random_mohe_cmd.finish("摩诃功能未启用，请管理员使用 /启用 摩诃 开启")

    count = random.randint(3, 5)
    selected = random.sample(MOHE_DATA, min(count, len(MOHE_DATA)))

    for i, item in enumerate(selected):
        await bot.send(event, _to_message(item))
        if i < len(selected) - 1:
            await asyncio.sleep(random.uniform(1, 3))


# ==================== 自动触发 ====================
async def _auto_mohe():
    """自动触发：向所有启用了摩诃的群发送随机摩诃语录"""
    bots = get_bots()
    if not bots:
        return

    bot: Bot = list(bots.values())[0]  # type: ignore

    try:
        groups = await bot.get_group_list()
    except Exception:
        return

    for group_info in groups:
        group_id = str(group_info["group_id"])

        if not is_feature_enabled("mohe", group_id):
            continue

        count = random.randint(3, 5)
        selected = random.sample(MOHE_DATA, min(count, len(MOHE_DATA)))

        for i, item in enumerate(selected):
            try:
                await bot.send_group_msg(group_id=int(group_id), message=_to_message(item))
            except Exception:
                break
            if i < len(selected) - 1:
                await asyncio.sleep(random.uniform(1, 3))

        # 不同群之间间隔一下，避免风控
        await asyncio.sleep(random.uniform(2, 5))


def _schedule_today():
    """为今天安排最多 2 次随机自动触发"""
    now = datetime.now()

    # 移除旧的自动触发任务
    for job_id in ("mohe_auto_0", "mohe_auto_1"):
        job = scheduler.get_job(job_id)
        if job:
            job.remove()

    # 在 9:00-22:00 之间随机选 2 个不重复的小时
    chosen_hours = sorted(random.sample(range(9, 22), 2))

    for i, h in enumerate(chosen_hours):
        m = random.randint(0, 59)
        run_time = now.replace(hour=h, minute=m, second=0, microsecond=0)

        # 只安排还没过去的时间点
        if run_time <= now:
            continue

        scheduler.add_job(
            _auto_mohe,
            "date",
            run_date=run_time,
            id=f"mohe_auto_{i}",
            replace_existing=True,
        )


# 每天 0:05 重新安排当天的自动触发
scheduler.add_job(
    _schedule_today,
    "cron",
    hour=0,
    minute=5,
    id="mohe_daily_reschedule",
    replace_existing=True,
)

# 启动时也安排一次当天的自动触发
driver = get_driver()


@driver.on_startup
async def _on_startup():
    _schedule_today()
