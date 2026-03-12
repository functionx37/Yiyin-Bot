# toggle（功能管理）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **命令**：`/功能列表`、`/启用 <功能名>`、`/禁用 <功能名>`
- **行为**：按群管理功能开关；并通过**全局预处理器**在每条群消息的 Matcher 执行前拦截，未启用或已禁用的功能不执行。

---

## 实现要点

### 配置

- **功能注册表**：`config/features.json`
  - `plugins`：默认启用，可按群禁用（如塔罗牌、群友语录、翻译等）。
  - `optin`：默认关闭，需在群内 `/启用`（如伪造消息）。
  - `hidden`：默认关闭且不在功能列表中展示，需手动启用（如『世界通知』『摩诃』）。
- **运行时状态**：`data/toggle/config.json`
  - `disabled[group_id]`：该群已禁用的插件 key 列表。
  - `enabled[group_id]`：该群已启用的 opt-in/hidden 功能 key 列表。

### 预处理器

- 使用 `run_preprocessor`，在每个 Matcher 收到群消息时执行。
- 根据 Matcher 所属插件解析出 **plugin_key**（全名匹配 → 子模块前缀 → 包名最后一段，如 `yiyin.tarot` → `tarot`）。
- 若 plugin_key 在 `plugins` 且该群在 `disabled` 中 → 抛出 `IgnoredException`。
- 若 plugin_key 在 `optin` 或 `hidden` 且该群不在 `enabled` 中 → 抛出 `IgnoredException`。
- toggle 自身与未在 features 中注册的插件不拦截。

### 对外接口

- **`is_feature_enabled(feature_key: str, group_id: str) -> bool`**
  - 供其他插件判断某 opt-in/hidden 功能是否在某群已启用。
  - 例：塔罗牌中『世界通知』、摩诃 均通过此函数判断。
- **`is_plugin_enabled(plugin_key: str, group_id: str) -> bool`**
  - 供其他插件判断某 plugins 功能是否在某群已启用（默认启用，被禁用则返回 False）。
  - 例：自动食物收集中『食物自动拾取』通过此函数判断是否执行后续处理。

### 权限

- `/启用`、`/禁用` 仅 **SUPERUSER | GROUP_ADMIN | GROUP_OWNER** 可执行。

---

## 扩展

新插件若需纳入开关：

- 默认启用、可被群禁用：在 `config/features.json` 的 `plugins` 中增加 `"模块名": "显示名"`。
- 默认关闭、需群内启用：在 `optin` 或 `hidden` 中增加一项。`hidden` 不会在『功能列表』中显示名称。
