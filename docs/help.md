# help（帮助菜单）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **命令**：`@Bot /help`
- **行为**：生成帮助菜单，以**合并转发消息**（假聊天记录）形式发送，每条节点对应一个功能模块的说明。

---

## 实现要点

- **数据源**：`assets/documents/help.json`
  - 结构：模块名 `module` + 功能列表 `function`，每项含 `command`、`description`。
- **流程**：读取 JSON → 遍历模块，为每个模块组装一段文本（模块名 + 各命令与描述）→ 用 `send_group_forward_msg` 发送多段节点，显示为 Bot 发出的『聊天记录』。
- **依赖**：NoneBot OneBot V11 的 `GroupMessageEvent`、`MessageSegment`、`to_me` 规则。

---

## 扩展

新增功能或命令时，需同步修改 `assets/documents/help.json`，以便在 `/help` 中展示。
