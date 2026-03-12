"""
梗图子功能：强强
- 命令：/强强 （文本1）（文本2）（文本3）
- 基于 bibi.jpg 在三个箭头上方绘制文本，直接发送不存储
"""

import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

# ==================== 资源路径 ====================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BIBI_IMAGE_PATH = _PROJECT_ROOT / "assets" / "images" / "meme" / "bibi.jpg"

# ==================== 字体（微软雅黑，项目内 assets/fonts/） ====================
# 使用加粗体 msyhbd.ttc，无自带加粗则用 msyh.ttc
FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "msyhbd.ttc"
FONT_PATH_FALLBACK = _PROJECT_ROOT / "assets" / "fonts" / "msyh.ttc"


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    path = FONT_PATH if FONT_PATH.exists() else FONT_PATH_FALLBACK
    if not path.exists():
        raise FileNotFoundError(
            f"未找到微软雅黑字体，请将 msyh.ttc / msyhbd.ttc 放到 assets/fonts/ 目录"
        )
    return ImageFont.truetype(str(path), size)


# ==================== 解析 （文本1）（文本2）（文本3） ====================
def _parse_strong_args(text: str) -> list[str]:
    """解析中文括号内的三组文本，不足补空字符串。支持括号内直接换行。"""
    # 匹配 （...） 或 （） 即允许空；DOTALL 使 . 匹配换行，支持多行输入
    parts = re.findall(r"（(.*?)）", text, re.DOTALL)
    while len(parts) < 3:
        parts.append("")
    return parts[:3]


# ==================== 强强图片生成（位置与字号，可手调） ====================
# 以下均为相对图片宽高的比例，取值 0.0～1.0。
#
# _TEXT_X_RATIOS：三段文字的水平中心位置（0=最左，1=最右）
#   - 第 1 个：往左调就减小，往右调就增大
#   - 第 2 个：同上
#   - 第 3 个：同上
# _TEXT_Y_RATIO：行数最多的那段文字的『底部』所在垂直位置（0=顶部，1=底部）
#   - 其余两段与该段垂直中心对齐
# _FONT_SIZE_RATIO：字号 = 图宽 × 该比例（再被 MIN/MAX 限制）
#   - 数值越大字越大，例如 1/16 比 1/22 大
# _FONT_SIZE_MIN / _FONT_SIZE_MAX：字号的像素上下限
# _LINE_SPACING：多行文本行距倍数（1.0=无间隙，1.3=1.3 倍行高）
#
_TEXT_X_RATIOS = (0.13, 0.50, 0.86)   # 左、中、右（1 再往左一点，2 再往右一点）
_TEXT_Y_RATIO = 0.15                   # 整体往下一点对准箭头
_FONT_SIZE_RATIO = 1 / 17              # 字号稍小一点
_FONT_SIZE_MIN = 18
_FONT_SIZE_MAX = 96
_LINE_SPACING = 1.25                   # 多行时行距


def _draw_bibi(texts: list[str]) -> bytes:
    """在 bibi.jpg 上绘制三处文本，返回 PNG 字节。画布按需扩展，三段文字垂直中心对齐。"""
    if not BIBI_IMAGE_PATH.exists():
        raise FileNotFoundError(f"模板图片不存在：{BIBI_IMAGE_PATH}")

    base_img = Image.open(BIBI_IMAGE_PATH).convert("RGB")
    w, h = base_img.size

    font_size = max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, int(w * _FONT_SIZE_RATIO)))
    font = _get_font(font_size)
    line_height = int(font_size * _LINE_SPACING)
    spacing = line_height - font_size

    # 第一遍：计算每段文字的 bbox 和位置（相对原图）
    draw_tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    infos: list[tuple[int, int, int, int]] = []  # (cx, tw, th, center_y) 或 (cx, 0, 0, 0) 空段
    cy_ref = int(h * _TEXT_Y_RATIO)

    for i, s in enumerate(texts):
        if not s:
            infos.append((0, 0, 0, 0))
            continue
        cx = int(w * _TEXT_X_RATIOS[i])
        bbox = draw_tmp.textbbox((0, 0), s, font=font, spacing=spacing)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        infos.append((cx, tw, th, 0))

    # 行数最多的那段底在 cy_ref，其余与它垂直中心对齐
    th_max = max((th for _, _, th, _ in infos if th > 0), default=0)
    center_y = cy_ref - th_max // 2 if th_max else cy_ref

    for i in range(len(infos)):
        cx, tw, th, _ = infos[i]
        if th > 0:
            top_y = center_y - th // 2
            infos[i] = (cx, tw, th, top_y)

    # 计算画布扩展：文本可能超出原图左、右、上
    left_pad = right_pad = top_pad = 0
    for i, (cx, tw, th, top_y) in enumerate(infos):
        if th == 0:
            continue
        x_left = cx - tw // 2
        x_right = cx + tw // 2
        if x_left < 0:
            left_pad = max(left_pad, -x_left)
        if x_right > w:
            right_pad = max(right_pad, x_right - w)
        if top_y < 0:
            top_pad = max(top_pad, -top_y)

    new_w = w + left_pad + right_pad
    new_h = h + top_pad
    img = Image.new("RGB", (new_w, new_h), (255, 255, 255))
    img.paste(base_img, (left_pad, top_pad))
    draw = ImageDraw.Draw(img)

    fill = (0, 0, 0)
    stroke_width = max(1, font_size // 24)

    for i, s in enumerate(texts):
        if not s:
            continue
        cx, tw, th, top_y = infos[i]
        x = left_pad + cx - tw // 2
        y = top_pad + top_y
        draw.multiline_text(
            (x, y), s, font=font, fill=fill,
            stroke_width=stroke_width, stroke_fill=(255, 255, 255),
            spacing=spacing, align="center",
        )

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


# ==================== 命令 ====================
qiangqiang_cmd = on_command("强强", priority=10, block=True)


@qiangqiang_cmd.handle()
async def handle_qiangqiang(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await qiangqiang_cmd.finish(
            "用法：/强强 （文本1）（文本2）（文本3）\n"
            "用中文括号括起三段文字，中间可以为空，例如：/强强 （左）（中）（右）\n"
            "支持换行：可直接在消息里换行"
        )
    parts = _parse_strong_args(raw)
    try:
        out_bytes = _draw_bibi(parts)
    except FileNotFoundError as e:
        await qiangqiang_cmd.finish(str(e))
    except Exception as e:
        await qiangqiang_cmd.finish(f"生成失败：{e}")
    # 显式用 bot.send 发送图片，再 finish 结束，避免仅 finish(image) 时部分环境下不发出
    await bot.send(event, MessageSegment.image(out_bytes))
    await qiangqiang_cmd.finish()
