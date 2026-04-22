"""
NoneBot2 回复插件（多功能）
- 子模块 deny：否定回应（默认关闭，需 /启用 否定）
- 子模块 pick：选择回应（默认开启，随回应插件生效）
- 子模块 repetition：复读（默认关闭，需 /启用 复读）
"""

from yiyin.react import deny  # noqa: F401
from yiyin.react import pick  # noqa: F401
from yiyin.react import repetition  # noqa: F401
