# meme（表情包制作）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/对称 (左/右/上/下) [图片]**：按指定方向对称翻转图片，支持静图与动图。
- **/强强 （文本1）（文本2）（文本3）**：在「籽岷」bibi 模板的三个箭头上方绘制文本，生成表情图。
- **/随机表情 [文字/图片]**、**/<关键词> [文字/图片]**：由 **nonebot-plugin-memes** 提供。
- **<emoji>+<emoji>**：由 **nonebot-plugin-emojimix** 提供 emoji 合成。

---

## 实现要点

### symmetric（对称）

- **路径**：`yiyin/meme/symmetric.py`
- 下载用户发送或引用的图片，PIL 处理：静图做镜像或上下/左右拼接；动图按帧处理后写回 GIF。
- 资源限制：最大像素、最大 GIF 帧数、并发数；使用线程池执行避免阻塞事件循环。
- 方向：左/右/上/下，未指定默认「左」。

### ziming（强强）

- **路径**：`yiyin/meme/ziming.py`
- **模板**：`assets/images/ziming/bibi.jpg`
- 解析中文括号 `（...）` 内的三段文本，不足 3 段用空字符串补足。
- 在模板上按比例定位（_TEXT_X_RATIOS、_TEXT_Y_RATIO、_FONT_SIZE_RATIO 等）用微软雅黑绘制文字，输出 PNG 经 BytesIO 发送，不落盘。

### 扩展

- 新梗图子功能可在 `yiyin/meme/` 下新增模块（如 `xxx.py`），并在 `yiyin/meme/__init__.py` 中 `from yiyin.meme import xxx` 以被加载。
