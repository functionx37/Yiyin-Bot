# Yiyin-Bot 项目总览

本文档描述项目总体架构。各模块的详细功能与实现见单独文档。

---

## 1. 项目概述

**Yiyin-Bot** 是一个基于 **NoneBot2** 与 **NapCat** 的 QQ 群聊机器人。功能包括：塔罗牌占卜、群友语录、食物图鉴、表情包制作、翻译、数学求解、随机选择、贴表情、动漫识别、伪造消息、角色扮演、摩诃语录等。插件按群隔离数据，支持按群启用/禁用功能。

- **运行方式**：Docker Compose 部署，NoneBot 作为主进程，NapCat 作为 OneBot 协议端连接 QQ。
- **插件加载**：通过 `pyproject.toml` 的 `[tool.nonebot]` 指定插件目录 `yiyin`，并显式加载若干第三方插件。

---

## 2. 技术栈与依赖

| 类别     | 技术/库 |
|----------|----------|
| 框架     | NoneBot2、OneBot V11 适配器 |
| 协议端   | NapCat（Docker 镜像） |
| 包管理   | uv、pyproject.toml |
| 图像     | Pillow、pilmoji |
| 调度     | nonebot-plugin-apscheduler |
| 数据库/ORM | nonebot-plugin-orm（可选） |
| 第三方插件 | nonebot-plugin-memes、nonebot-plugin-emojimix、nonebot-plugin-anime-trace、nonebot-plugin-fakemsg |

---

## 3. 整体架构

### 3.1 入口与驱动

- **入口文件**：`bot.py`
  - 初始化 NoneBot，注册 OneBot V11 适配器。
  - 通过 `nonebot.load_from_toml("pyproject.toml")` 加载插件：扫描 `plugin_dirs = ["yiyin"]`，并加载 `[tool.nonebot.plugins]` 中列出的第三方插件。

### 3.2 插件来源

1. **自研插件**：`yiyin/` 下各子包，由 NoneBot 自动发现并加载。
2. **第三方插件**：在 `pyproject.toml` 的 `[tool.nonebot.plugins]` 中显式列出。

### 3.3 功能开关（toggle）

- **toggle** 插件通过 `run_preprocessor` 在所有群消息的 Matcher 执行前做统一拦截。
- 根据 `config/features.json` 将功能分为 **plugins**（默认启用可禁用）、**optin**（默认关闭需启用）、**hidden**（默认关闭且不在列表展示）。
- 其他插件可通过 `from yiyin.toggle import is_feature_enabled` 判断某 opt-in/hidden 功能是否在某群开启。

详见 [toggle.md](toggle.md)。

---

## 4. 目录结构

```
Yiyin-Bot/
├── bot.py                 # 入口
├── pyproject.toml         # 项目与 NoneBot 插件配置
├── docker-compose.yml     # NoneBot + NapCat 服务编排
├── config/                # 静态/可编辑配置
├── data/                  # 运行时数据（需持久化）
├── assets/                # 静态资源（documents、fonts、images）
├── scripts/               # 运维脚本
└── yiyin/                 # 自研插件包
    ├── help/              # 帮助菜单 → [help.md](help.md)
    ├── toggle/            # 功能管理 → [toggle.md](toggle.md)
    ├── tarot/             # 塔罗牌 → [tarot.md](tarot.md)
    ├── quotes/            # 群友语录 → [quotes.md](quotes.md)
    ├── food/              # 食物图鉴 → [food.md](food.md)
    ├── magazine/          # 群刊 → [magazine.md](magazine.md)
    ├── meme/              # 表情包制作 → [meme.md](meme.md)
    ├── translate/        # 翻译 → [translate.md](translate.md)
    ├── wolfram/           # 数学求解 → [wolfram.md](wolfram.md)
    ├── choose/            # 随机选择 → [choose.md](choose.md)
    ├── emoji_reaction/    # 贴表情 → [emoji_reaction.md](emoji_reaction.md)
    ├── roleplay/          # 角色扮演 → [roleplay.md](roleplay.md)
    ├── mohe/              # 摩诃语录 → [mohe.md](mohe.md)
    └── llmapi/            # 公共 LLM 调用 → [llmapi.md](llmapi.md)
```

---

## 5. 各模块文档索引

| 模块 | 文档 | 说明 |
|------|------|------|
| help | [help.md](help.md) | 帮助菜单（@Bot /help） |
| toggle | [toggle.md](toggle.md) | 功能列表/启用/禁用与预处理器 |
| tarot | [tarot.md](tarot.md) | 塔罗牌抽牌、十连、占卜、世界通知 |
| quotes | [quotes.md](quotes.md) | 群友语录与聊天截图生成 |
| food | [food.md](food.md) | 食物图鉴与「吃什么」 |
| magazine | [magazine.md](magazine.md) | 群刊（语录+食物合并转发，每群每日一次） |
| meme | [meme.md](meme.md) | 对称图、强强模板等表情包 |
| translate | [translate.md](translate.md) | 腾讯云翻译与对外接口 |
| wolfram | [wolfram.md](wolfram.md) | WolframAlpha 数学求解 |
| choose | [choose.md](choose.md) | 随机选择与特判规则 |
| emoji_reaction | [emoji_reaction.md](emoji_reaction.md) | 贴表情/发表情 |
| roleplay | [roleplay.md](roleplay.md) | 37 角色扮演与 LLM 对话 |
| mohe | [mohe.md](mohe.md) | 摩诃语录与定时推送 |
| llmapi | [llmapi.md](llmapi.md) | 云雾 API 封装（无命令） |

---

## 6. 配置与数据

- **环境变量**：如 `ONEBOT_ACCESS_TOKEN`、`YUNWU_API_KEY`、`TENCENT_SECRET_*`、`WOLFRAM_APPID` 等，见各模块文档及 `.env.example`。
- **功能列表**：用户可见命令以 `assets/documents/help.json` 为准；开关逻辑以 `config/features.json` 为准。
- **持久化**：`data/` 需挂载或备份（Docker 中 `./data:/app/data`）。

---

## 7. 部署与运行

- **Docker**：`docker compose up -d --build`；NapCat WebUI 端口 6099；QQ 登录与反向 WS 见项目 README。
- **更新**：`git pull` 后 `docker compose up -d --build`；数据同步用 `scripts/sync-data.sh`。

---

## 8. 扩展开发建议

1. **新增自研插件**：在 `yiyin/` 下新建包；若需按群开关，在 `config/features.json` 中登记。
2. **新增第三方插件**：在 `pyproject.toml` 的 `[tool.nonebot.plugins]` 与依赖中加入该包。
3. **复用能力**：翻译用 `yiyin.translate.translate_text`；LLM 用 `yiyin.llmapi.chat_completion`；功能启用判断用 `yiyin.toggle.is_feature_enabled`。
4. **资源路径**：统一用 `Path(__file__).resolve().parent...` 定位项目根或 `assets/`、`config/`。
5. **帮助与开关**：新命令在 `assets/documents/help.json` 中补充；需按群关闭或默认关闭的在 `config/features.json` 的 plugins/optin/hidden 中增加项。

---

*文档随项目迭代更新，以代码与配置文件为准。*
