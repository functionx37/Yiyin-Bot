"""Yiyin Bot - NoneBot2 QQ 机器人"""
# 在导入 nonebot 及插件前过滤 jieba 的 Python 3.12 转义序列警告
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning, module="jieba")

from pathlib import Path

try:
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parent
    if (root / ".env.prod").exists():
        load_dotenv(root / ".env.prod")
    elif (root / ".env").exists():
        load_dotenv(root / ".env")
except ImportError:
    pass

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import default_filter, default_format, logger

LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.add(
    LOG_DIR / "nonebot_{time:YYYY-MM-DD}.log",
    level="INFO",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    format=default_format,
    filter=default_filter,
)

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
