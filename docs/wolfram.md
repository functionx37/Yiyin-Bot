# wolfram（数学求解）

[← 总览](ARCHITECTURE.md)

---

## 功能

- **/算 <问题描述>**：调用 WolframAlpha 求解数学/通用问题，以**合并转发消息**返回多段结果（标题 + 图片/纯文本）。

---

## 实现要点

### API

- **WolframAlpha Full Results API**：`https://api.wolframalpha.com/v2/query`，参数 `appid`、`input`、`output=json`、`units=metric`。
- 环境变量：**`WOLFRAM_APPID`**。

### 中文查询

- 若输入包含非 ASCII（如中文），先调用 `yiyin.translate.translate_text(query, target="en")` 将查询翻译成英文，再用英文请求 API，提高识别率。

### 结果组装

- 解析返回 JSON 的 `queryresult.pods`，每个 pod 含 `title`、`subpods`（每项可有 `img.src`、`plaintext`）。
- 构建多段「假聊天记录」节点，每段对应一个 pod，图文拼接后通过 `send_group_forward_msg`（群）或 `send_private_forward_msg`（私聊）发送。

### 错误处理

- `success` 为假时提示无法理解，可附带 `tips.text` 建议；无 pods 时提示换表述；请求超时单独提示。
