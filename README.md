# Yiyin Bot

本项目是一个基于 **NoneBot2** 和 **NapCat** 框架的QQ聊天机器人。  
Python包依赖使用 **uv** 进行管理。

## 部署步骤  

1. 创建 `.env.prod` 配置  
    ```
    cp .env.example .env.prod
    # 编辑 .env.prod，填入你的 ONEBOT_ACCESS_TOKEN
    ```
2. 启动容器  
    ```
    docker compose up -d --build
    ```
3. 登录 QQ（首次需要）
    * 打开浏览器访问 `<your-IP>:6099/webui`
    * 获取Token： `docker logs yiyin-napcat 2>&1 | grep -i token`
    * 进入网络配置，添加一个 WebSocket 客户端（反向 WS）
    * URL：`ws://nonebot:8080/onebot/v11/ws`
    * Token：`.env.prod` 中设置的 `ONEBOT_ACCESS_TOKEN`

## 更新插件
```
git pull
docker compose up -d --build
```