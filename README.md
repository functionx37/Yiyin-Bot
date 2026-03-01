# Yiyin Bot

本项目是一个基于 **NoneBot2** 和 **NapCat** 框架的QQ聊天机器人。  
Python包依赖使用 **uv** 进行管理。

## 部署步骤  

1. 安装 NapCat 和 uv
    ```bash
    curl -o \
    napcat.sh \
    https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh \
    && bash napcat.sh
    ```
    ```bash
    pipx install uv
    uv sync
    ```
2. 克隆本仓库
    ```bash
    git clone https://github.com/functionx37/Yiyin-Bot.git Yiyin-Bot
    cd Yiyin-Bot
    ```
3. 配置环境变量  
    ```bash
    cp .env.example .env.prod
    # 编辑 .env.prod，填入你的 ONEBOT_ACCESS_TOKEN 等
    ```
4. 配置 NapCat
    ```bash
        mkdir napcat
        cd napcat
        napcat
        # 根据提示新建一个网络配置，添加一个 WebSocket 客户端（反向 WS）
        # URL：`ws://nonebot:8080/onebot/v11/ws`
        # Token：`.env.prod` 中设置的 `ONEBOT_ACCESS_TOKEN`
        # 启用该配置，并根据提示扫码登录
    ```
5. 启动机器人
   ```bash
   ./scripts/start.sh
   ```
   一键后台启动 bot.py 和 napcat。停止：`./scripts/stop.sh`

## 功能列表
[点击查看](assets/documents/help.json)

## 数据同步  
项目提供 `scripts/sync-data.sh`，可在 `.env.prod` 中配置默认远程，也可以用参数传递，需要 SSH 能登录。

请注意，脚本为镜像同步，目标端会被覆盖。

```bash
# 把本机推到远程
./scripts/sync-data.sh push
# 从远程拉取到本机
./scripts/sync-data.sh pull
# 只查看会同步哪些文件（不实际传输）
./scripts/sync-data.sh diff
```