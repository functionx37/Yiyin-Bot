# 通用图片资源保护

位于项目根目录，避免被 NoneBot 当成业务插件加载。

- `admission.py`：系统内存、PSI、cgroup 剩余预算与恢复等待。
- `jobs.py`：所有图片工具共用的并发队列、分块下载、输入预算、子进程监督和临时文件清理。
- `worker.py`：独立子进程入口，设置地址空间硬预算，分发可信的图片操作。
- `renderers/`：不导入 Bot 插件的绘图逻辑，供独立进程执行。
- `integrations.py`：启动时接管第三方表情插件共享生成入口，并对其他图片插件进行接单前检查；不修改 site-packages。
- `snapshot.py`：每 30 秒的资源记录，由系统定时器调用。

## 接入指令

优先在下载、生成、发送整个过程持有同一个 ImageJob，让文件保持到发送完成。

```python
from resource_guard import ImageJob, ResourceBusy

try:
    async with ImageJob() as job:
        source = await job.download(url)
        output = await job.render('symmetric', {'source': source, 'direction': '左'})
        await matcher.send(MessageSegment.image(output.as_uri()))
except ResourceBusy as error:
    await matcher.finish(str(error))
```

已有输入的工具可使用 image_job(operation, payload) 上下文。需要字节的调用者可用 render_bytes(operation, payload)；读取结果前会检查动态发送预算。所有接口共用队列，不能为每个工具另建并发许可。

新增绘图工具时，把不涉及 Bot 交互的函数放入 renderers/，在 worker.execute 注册固定操作名。参数支持 JSON 基本类型、Path 和 bytes；bytes 会先写入受预算约束的临时文件。

## 当前覆盖

独立子进程：对称、普通和随机表情包制作、强强、想、盐巴、男娘、聊天截图、黑白成绩图、塔罗牌。

表情预览、列表、统计图、食物拼图、emojimix 和 fakemsg 当前具有接单前检查及系统进程组保护，尚未全部迁移内部生成到可终止子进程。接单检查不能替代执行期间的资源隔离。

第三方表情插件升级后，应检查 integrations.py 使用的共享 process 函数签名与消息接口。启动错误会出现在系统服务日志中。

测试代码不随项目保留；验证使用临时进程和生成图片，不发 QQ 消息。
