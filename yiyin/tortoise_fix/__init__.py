"""Tortoise ORM 修复插件：当所有模型使用独立数据库时，为默认 app 添加占位模型。

nonebot_plugin_learning_chat 使用 add_model(db_name="learning_chat", ...) 注册到独立数据库，
导致默认 app 的 models 为空。Tortoise ORM 要求每个 app 的 models 必须非空，会抛出：
  ConfigurationError: AppConfig.models must be a non-empty list of strings

本插件向默认 app 注册一个占位模型，避免该错误。
"""
from nonebot import require

require("nonebot_plugin_tortoise_orm")
from nonebot_plugin_tortoise_orm import add_model

# 向默认 app 添加占位模型，必须在插件加载时执行（早于 driver.on_startup）
add_model("yiyin.tortoise_fix.models")
