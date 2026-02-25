# mohe（摩诃）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/随机摩诃**：主动发送 3～5 条随机摩诃语录（文本或图片），逐条发送，每条间隔 1～3 秒。
- **自动推送**：每天在 9:00～22:00 之间随机 2 个时间点，向所有**已启用「摩诃」**的群各发 3～5 条随机语录；默认关闭，需 `/启用 摩诃`（在 toggle 的 **hidden** 中，功能列表不展示名称）。

---

## 实现要点

### 数据源

- **文本**：`assets/documents/mohe.json`，字符串列表。
- **图片**：`assets/images/mohe/` 下 `.jpg`、`.jpeg`、`.png`、`.gif`、`.webp`，按文件名排序加入池。
- 文本与图片合并为统一池 **MOHE_DATA**，随机时从中抽样。

### 定时任务

- 依赖 **nonebot_plugin_apscheduler**。
- **每日 0:05** 执行 `_schedule_today()`：移除旧的 `mohe_auto_0`、`mohe_auto_1`，在 9～21 点中随机选 2 个不重复的小时，再各随机 0～59 分，若该时间点尚未过去则添加一次 `date` 任务执行 `_auto_mohe`。
- **启动时**：`driver.on_startup` 也执行一次 `_schedule_today()`，为当天安排自动推送。

### 自动推送逻辑

- `_auto_mohe()`：取当前 bot，拉取群列表；对每个群若 `is_feature_enabled("mohe", group_id)` 则在该群发送 3～5 条随机内容，每条间隔 1～3 秒，群与群之间再间隔 2～5 秒，降低风控风险。
