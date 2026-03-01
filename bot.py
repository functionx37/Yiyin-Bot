"""Yiyin Bot - NoneBot2 QQ 机器人"""
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

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
