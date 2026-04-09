"""
NoneBot2 表情包制作插件（多功能）
- /对称 [左/右/上/下] [图片]：对称翻转图片
- /强强 （文本1）（文本2）（文本3）：基于 bibi 模板在三个箭头上方写字并发送
- /想 <文本>：在 motis 左上角气泡中填入文字，支持 emoji，自动调整字号与换行
- /男娘 [@群友]：将 xnn 模板叠在头像上，无 @ 则对发送者
"""

from yiyin.meme import symmetric  # noqa: F401
from yiyin.meme import ziming     # noqa: F401
from yiyin.meme import motis      # noqa: F401
from yiyin.meme import xnn        # noqa: F401
