"""Yiyin Bot: NoneBot2 with the official QQ WebSocket adapter."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
# Resolve plugin paths, assets and environment files from the project directory.
os.chdir(ROOT)
env_path = ROOT / ".env.prod"
load_dotenv(env_path if env_path.exists() else ROOT / ".env", override=False)

import nonebot
from nonebot.adapters.qq import Adapter as OfficialQQAdapter
from nonebot.adapters.qq.event import GroupAtMessageCreateEvent, GroupMessageCreateEvent
from nonebot.compat import type_validate_python
from nonebot.drivers import Driver
from nonebot.log import default_filter, default_format, logger


class GroupMessageCompat(GroupMessageCreateEvent):
    # Official event examples only contain group_openid. Do not invent a
    # group_id or infer permissions; all business routing uses group_openid.
    group_id: str | None = None


class GroupAtMessageCompat(GroupAtMessageCreateEvent):
    group_id: str | None = None


class QQAdapter(OfficialQQAdapter):
    @staticmethod
    def payload_to_event(payload):
        compatible = {
            "GROUP_AT_MESSAGE_CREATE": GroupAtMessageCompat,
            "GROUP_MESSAGE_CREATE": GroupMessageCompat,
        }.get(payload.type)
        if compatible:
            return type_validate_python(compatible, {"event_id": payload.id, **payload.data})
        return OfficialQQAdapter.payload_to_event(payload)


def initialize() -> Driver:
    """Load configuration, the QQ adapter, and the tarot plugin."""
    os.environ.setdefault("DRIVER", "~none+~httpx+~websockets")
    nonebot.init()
    from nonebot.adapters.qq.config import Config

    qq_config = nonebot.get_plugin_config(Config)
    if not qq_config.qq_bots or any(
        not item.id
        or not item.secret
        or item.id == "YOUR_APP_ID"
        or item.secret == "YOUR_APP_SECRET"
        for item in qq_config.qq_bots
    ):
        raise RuntimeError("请在 .env.prod 的 QQ_BOTS 中填写真实 AppID 和 AppSecret。")
    if any(not item.use_websocket for item in qq_config.qq_bots):
        raise RuntimeError("本项目仅支持 WebSocket，请将 QQ_BOTS 的 use_websocket 设为 true。")
    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "nonebot_{time:YYYY-MM-DD}.log",
        level="INFO",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        format=default_format,
        filter=default_filter,
    )
    driver = nonebot.get_driver()
    driver.register_adapter(QQAdapter)
    nonebot.load_from_toml("pyproject.toml")
    if nonebot.get_plugin_by_module_name("yiyin.tarot") is None:
        raise RuntimeError("塔罗牌插件加载失败，请检查 assets 资源及启动日志。")

    return driver


if __name__ == "__main__":
    initialize()
    nonebot.run()
