"""
NoneBot2 回复插件（多功能）
- 子模块 deny：否定回应（默认关闭，需 /启用 否定）
- 子模块 must：一定回应（默认开启，随回应插件生效）
- 子模块 pick：选择回应（默认开启，随回应插件生效）
- 子模块 react：井号随机回应（默认开启，随回应插件生效）
- 子模块 repetition：重复复读（默认关闭，需 /启用 复读）
- 子模块 random：乱序复读（默认关闭，需 /启用 乱序复读）
"""

from yiyin.react import deny  # noqa: F401
from yiyin.react import must  # noqa: F401
from yiyin.react import pick  # noqa: F401
from yiyin.react import random  # noqa: F401
from yiyin.react import react  # noqa: F401
from yiyin.react import repetition  # noqa: F401
