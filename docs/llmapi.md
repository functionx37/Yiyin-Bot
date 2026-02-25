# llmapi（公共 LLM 调用）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **无命令**：本模块不注册任何 Matcher，仅作为**公共能力**供其他插件调用。
- **作用**：统一封装 OpenAI 兼容的 Chat Completions 接口（本项目使用云雾 API 中转），便于塔罗占卜、角色扮演等复用。

---

## 实现要点

### 配置

- **环境变量**：`YUNWU_API_KEY`（必填）、`YUNWU_BASE_URL`（默认 `https://yunwu.ai/v1`）。
- 未配置 API Key 时，请求不发出，直接返回 `None`。

### 接口

- **`chat_completion(messages, *, model=..., temperature=..., max_tokens=..., top_p=..., timeout=..., **kwargs) -> str | None`**
  - **messages**：`[{"role": "user"|"assistant"|"system", "content": "..."}]`。
  - **model**：默认 `claude-haiku-4-5-20251001`。
  - **temperature**、**max_tokens**、**top_p**：常见采样与生成长度参数。
  - **返回值**：成功时为 `choices[0].message.content`；失败（未配置、非 200、无 choices 等）返回 `None`。

### 调用方

- **tarot**：占卜时构建 system + user 消息，调用后得到解读文案。
- **roleplay**：角色扮演时构建 system（prompt 文件）+ 历史 + 当前用户消息，调用后得到 37 的回复。
