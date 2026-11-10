"""Yiyin Bot: NoneBot2 with the official QQ HTTPS Webhook adapter."""

import binascii
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
# Resolve plugin paths, assets and environment files from the project directory.
os.chdir(ROOT)
env_path = ROOT / ".env.prod"
load_dotenv(env_path if env_path.exists() else ROOT / ".env", override=False)

import nonebot
from nonebot.adapters.qq import Adapter as OfficialQQAdapter
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.models import FriendAuthor, GroupMemberAuthor
from nonebot.compat import type_validate_python
from nonebot.drivers import URL, Driver, HTTPServerSetup, Response
from nonebot.log import default_filter, default_format, logger


class FriendAuthorCompat(FriendAuthor):
    id: str | None = None


class GroupAuthorCompat(GroupMemberAuthor):
    id: str | None = None
    bot: bool | None = None
    member_role: Literal["member", "admin", "owner"] | None = None


class C2CMessageCompat(C2CMessageCreateEvent):
    author: FriendAuthorCompat


class GroupMessageCompat(GroupMessageCreateEvent):
    # Official event examples only contain group_openid. Do not invent a
    # group_id or infer permissions; all business routing uses group_openid.
    group_id: str | None = None
    author: GroupAuthorCompat


class GroupAtMessageCompat(GroupAtMessageCreateEvent):
    group_id: str | None = None
    author: GroupAuthorCompat


class QQAdapter(OfficialQQAdapter):
    def _check_signature(self, bot, request):
        # The upstream adapter decodes hex outside its try/except. Reject
        # malformed public requests rather than turning them into HTTP 500s.
        try:
            return super()._check_signature(bot, request)
        except (binascii.Error, ValueError):
            return Response(403, content="Invalid signature")

    @staticmethod
    def payload_to_event(payload):
        compatible = {
            "C2C_MESSAGE_CREATE": C2CMessageCompat,
            "GROUP_AT_MESSAGE_CREATE": GroupAtMessageCompat,
            "GROUP_MESSAGE_CREATE": GroupMessageCompat,
        }.get(payload.type)
        if compatible:
            return type_validate_python(compatible, {"event_id": payload.id, **payload.data})
        return OfficialQQAdapter.payload_to_event(payload)


def initialize() -> Driver:
    """Load configuration, the QQ adapter, and the plugins."""
    os.environ.setdefault("DRIVER", "~fastapi+~httpx")
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ.setdefault("PORT", "8080")
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
    if any(item.use_websocket for item in qq_config.qq_bots):
        raise RuntimeError("本项目使用 Webhook，请将 QQ_BOTS 的 use_websocket 设为 false。")
    if not qq_config.qq_verify_webhook:
        raise RuntimeError("公网回调必须开启验签，请将 QQ_VERIFY_WEBHOOK 设为 true。")
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

    async def health(_request):
        return Response(200, content='{"status":"ok","transport":"webhook"}',
                        headers={"Content-Type": "application/json"})

    driver.setup_http_server(HTTPServerSetup(URL("/healthz"), "GET", "Health", health))
    nonebot.load_from_toml("pyproject.toml")
    if nonebot.get_plugin_by_module_name("yiyin.tarot") is None:
        raise RuntimeError("塔罗牌插件加载失败，请检查 assets 资源及启动日志。")
    if nonebot.get_plugin_by_module_name("yiyin.choose") is None:
        raise RuntimeError("选择插件加载失败，请检查启动日志。")

    return driver


if __name__ == "__main__":
    initialize()
    nonebot.run()
