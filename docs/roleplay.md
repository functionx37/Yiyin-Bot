# roleplay（角色扮演）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **身份**：37 — 重返未来：1999。
- **被 @ 时**：必定回复一条 LLM 生成的内容，并 @ 回去。
- **未被 @ 时**：以低概率随机参与群聊（可配置），且受冷却时间限制。
- **默认关闭**：需在群内 `/启用 角色扮演`（在 toggle 的 optin 中）。

---

## 实现要点

### 开关与规则

- 通过 **`is_feature_enabled("roleplay", str(group_id))`** 判断是否处理该群消息；Matcher 使用自定义 Rule 只处理已启用群。
- 忽略 Bot 自己发出的消息（通过 `_self_id` 判断）。

### 上下文与调用

- **历史**：每群维护 `deque`，保存最近若干条 user/assistant 消息（格式如「昵称：内容」），条数由配置 `max_context_messages` 限制。
- **构建消息**：system = `yiyin/roleplay/prompt.txt`；再拼接历史中最近 N 条；最后一条为当前用户「昵称：文本」。
- **LLM**：`yiyin.llmapi.chat_completion`，模型、temperature、max_tokens 等来自配置。
- 回复后：将 assistant 回复追加到该群历史，并更新该群「上次随机回复时间」用于冷却。

### 配置

- **config/roleplay.json**：`model`、`reply_probability`、`max_context_messages`、`cooldown_seconds`、`max_reply_tokens`、`temperature` 等；文件变更后自动重新加载。
- **yiyin/roleplay/prompt.txt**：角色设定与对话风格。

### 冷却与随机

- 随机回复（未被 @）时：若与上次随机回复间隔小于 `cooldown_seconds` 则不再回复；否则以 `reply_probability` 概率回复。
- 被 @ 时不受冷却与概率限制，必定回复（LLM 失败时回复固定兜底文案）。
