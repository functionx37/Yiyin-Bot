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

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

# Tortoise ORM 1.x：ASGI lifespan 在后台任务初始化，请求在另一任务执行，
# 需 _enable_global_fallback 才能跨任务访问数据库
from tortoise import Tortoise

_orig_init = Tortoise.init

async def _patched_init(*args, _enable_global_fallback=True, **kwargs):
    return await _orig_init(*args, _enable_global_fallback=_enable_global_fallback, **kwargs)

Tortoise.init = _patched_init

if __name__ == "__main__":
    nonebot.run()
