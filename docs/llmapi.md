# llmapi（公共 LLM 调用）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **无命令**：本模块不注册任何 Matcher，仅作为**公共能力**供其他插件调用。
- **作用**：统一封装 OpenAI 兼容的 Chat Completions 接口（本项目使用云雾 API 中转，[文档](https://yunwu.apifox.cn/)），便于塔罗占卜、LLM 群友等复用。
- **识图（Vision）**：支持 GPT 等模型的多模态输入，可传入图片 URL 进行图片理解。

---

## 实现要点

### 配置

- **环境变量**：`YUNWU_API_KEY`（必填）、`YUNWU_BASE_URL`（默认 `https://yunwu.ai/v1`）。
- 未配置 API Key 时，请求不发出，直接返回 `None`。

### 接口

- **`chat_completion(messages, *, model=..., temperature=..., max_tokens=..., top_p=..., timeout=..., **kwargs) -> str | None`**
  - **messages**：`[{"role": "user"|"assistant"|"system", "content": "..."}]`。
  - **content** 可为纯文本 `str`，或识图时的多模态数组：
    - `[{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"https://..."}}]`
  - **model**：默认 `claude-haiku-4-5-20251001`。识图需用 Vision 模型（如 `gpt-4o-mini`、`gpt-4o`）。
  - **temperature**、**max_tokens**、**top_p**：常见采样与生成长度参数。
  - **返回值**：成功时为助手回复文本；失败返回 `None`。

- **`describe_image(prompt, image_url, *, model=..., max_tokens=..., timeout=...) -> str | None`**
  - 使用 Vision 模型理解图片并返回文本描述。
  - **prompt**：对图片的提问（如『简短描述这张图』）。
  - **image_url**：图片 URL，需公网可访问（jpeg/png/gif/webp）。
  - **model**：默认 `gpt-4o-mini`。
  - **返回值**：模型描述文本，失败返回 `None`。

### 调用方

- **tarot**：占卜时构建 system + user 消息，调用后得到解读文案。
- **llm_groupmate**（图片处理）：`describe_image` 将群消息中的图片转为文本描述供 LLM 使用。
