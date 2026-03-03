"""占位模型，满足 Tortoise 对默认 app 非空 models 的要求。"""
from tortoise import fields
from tortoise.models import Model


class Placeholder(Model):
    id: int = fields.IntField(pk=True)

    class Meta:
        table = "tortoise_fix_placeholder"
