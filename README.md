# Yiyin Bot

本项目是一个基于 **NoneBot2** 和 **NapCat** 框架的QQ聊天机器人。  
Python包依赖使用 **uv** 进行管理。

## 部署步骤  

1. 创建 `.env.prod` 配置  
    ```bash
    cp .env.example .env.prod
    # 编辑 .env.prod，填入你的 ONEBOT_ACCESS_TOKEN 等
    ```
2. 启动容器  
    ```bash
    docker compose up -d --build
    ```
3. 登录 QQ
    * 打开浏览器访问 `<your-IP>:6099/webui`
    * 获取Token： `docker logs yiyin-napcat 2>&1 | grep -i token`
    * 进入网络配置，添加一个 WebSocket 客户端（反向 WS）
    * URL：`ws://nonebot:8080/onebot/v11/ws`
    * Token：`.env.prod` 中设置的 `ONEBOT_ACCESS_TOKEN`

## 更新插件
```bash
git pull
docker compose up -d --build
```

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