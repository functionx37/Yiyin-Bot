# tarot（塔罗牌）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/抽塔罗牌**：随机抽一张大阿卡纳（0–21），随机正位/逆位，发送卡面图与释义。
- **/抽十连**：一次抽 10 张牌，精简文本输出；每用户每天限用一次。
- **/占卜 (占卜方向) <引用抽十连结果>**：引用一条抽十连的结果，用 LLM 做塔罗占卜解读，可选占卜方向。
- **正位世界通知**：抽到正位「世界」时，若群已启用「世界通知」，则 @群主 并发送「世界！」。

---

## 实现要点

### 数据与资源

- **卡牌数据**：`assets/documents/tarot.json`
  - 每张牌：`id`、`name_zh`、`name_en`、`upright`、`reversed`（正/逆位释义）。
- **卡面图**：`assets/images/tarot/`，文件名 `0.png`～`21.png`；逆位时用 PIL 将图片旋转 180° 后发送。

### 抽十连限制

- `data/tarot/ten_draw_last_date.json` 记录每个 user_id 上次使用日期（YYYY-MM-DD）；同一天再次调用则提示「今天已经抽过十次了」。重启 Bot 后仍有效。

### 占卜

- 从 `event.reply` 中取抽十连的文本；命令中可带「占卜方向」一并传给 LLM。
- 系统 prompt：占卜师人设，要求 3–5 句话整体解读、可点出关键牌。
- 调用 `yiyin.llmapi.chat_completion`（如模型 `claude-haiku-4-5-20251001`），返回内容去掉首尾引号后回复。

### 世界通知

- 抽到牌 id=21 且正位时，若 `is_feature_enabled("world_notify", group_id)` 为真，则 `get_group_member_list` 取群主，发送 at + 「世界！」。
- 「世界通知」在 `config/features.json` 的 `hidden` 中，需群内单独启用。

---

## 依赖

- `yiyin.llmapi.chat_completion`
- `yiyin.toggle.is_feature_enabled`（世界通知）
