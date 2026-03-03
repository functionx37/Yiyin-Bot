"""Tortoise ORM 修复：learning_chat 使用独立数据库，导致默认 app 的 models 为空。

Tortoise 要求每个 app 的 models 非空，否则报 ConfigurationError。
本插件向默认 app 注册占位模型以通过校验。
"""
from nonebot import require

require("nonebot_plugin_tortoise_orm")
from nonebot_plugin_tortoise_orm import add_model

add_model("yiyin.tortoise_fix.models")
