"""占位模型，用于满足 Tortoise ORM 对默认 app 非空 models 的要求。"""
from tortoise import fields
from tortoise.models import Model


class Placeholder(Model):
    """占位模型，不存储实际业务数据。"""

    id: int = fields.IntField(pk=True)

    class Meta:
        table = "tortoise_fix_placeholder"
