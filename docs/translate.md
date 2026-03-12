# translate（翻译）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/翻译 <目标语言> <文本>**：将文本翻译为目标语言。
- 支持**引用消息**：引用一条消息后发送 `/翻译 <目标语言>`，则翻译被引用消息的纯文本。
- **支持语言**：中文、英文、日文（含『中/英/日』『zh/en/ja』等别名）。

---

## 实现要点

### API

- **腾讯云机器翻译**（TMT），TC3 签名；环境变量 `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`。
- 请求体含 `SourceText`、`Source`（可选 auto）、`Target`、`ProjectId` 等。

### 对外接口

- **`translate_text(text: str, target: str, source: str = "auto") -> str | None`**
  - 供其他插件调用，例如 **wolfram** 在查询含中文时先翻译成英文再请求 WolframAlpha。

### 语言映射

- 目标语言统一为 `zh` / `en` / `ja`；展示名用 `LANG_DISPLAY`（中文、英文、日文）。
