# emoji_reaction（贴表情）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/贴表情列表**：发送使用说明（来自 help.json 中「贴表情」模块）及部分表情预览图（合并转发）。
- **/贴 <ID> <引用消息>**：给引用的消息贴上指定 ID 的 QQ 系统表情（emoji like）。
- **/贴<数字>个 <引用消息>**：给引用消息随机贴上指定个数的表情（最多 20 个），并列出所贴 ID。
- **/发 <ID>**：发送对应 ID 的 QQ 系统表情（face）。
- **/发 随机**：从配置的 ID 范围内随机选一个表情发送。

---

## 实现要点

### 配置与资源

- **表情 ID 范围**：`config/emoji_reaction.json`，格式为区间列表，如 `[[0, 470], [500, 600]]`，用于构建随机池。
- **预览图**：`assets/images/emoji_list/` 下的 PNG，在「贴表情列表」中一并发出。

### API 依赖

- **贴表情**：调用 OneBot `set_msg_emoji_like`，参数 `message_id`、`emoji_id`。需协议端（如 NapCat）支持该 API。
- **发表情**：`MessageSegment.face(face_id)` 发送 QQ 表情；随机时从池中抽样，失败则重试若干次。

### 行为细节

- 「贴<N>个」时逐个调用 `set_msg_emoji_like`，每次间隔约 0.3s，避免风控；最后回复一行列出所贴表情 ID（每行 5 个）。
- 帮助文本从 `assets/documents/help.json` 中读取「贴表情」模块的 command/description 拼接而成。
