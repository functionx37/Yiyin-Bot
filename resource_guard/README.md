# 通用图片资源保护

位于项目根目录，避免被 NoneBot 当成业务插件加载。

- `admission.py`：系统内存、PSI、cgroup 剩余预算与恢复等待。cgroup 仅计入一半干净的非活跃文件缓存作为可回收额度，仍保留系统预留、80% 预算折扣和所有硬限额。
- `jobs.py`：所有图片工具共用的并发队列、分块下载、输入预算、子进程监督和临时文件清理。
- `worker.py`：独立子进程入口，设置地址空间硬预算，分发可信的图片操作。
- `renderers/`：不导入 Bot 插件的绘图逻辑，供独立进程执行。
- `integrations.py`：启动时接管第三方表情插件共享生成入口，仅按明确的绘图处理函数检查其他入口；不按整个业务插件模块拦截，不修改 site-packages。
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

独立子进程：图片处理（对称、倒放、加速、减速、翻转、固定角度转向、连续旋转）、普通和随机表情包制作、强强、想、盐巴、男娘、聊天截图、黑白成绩图、塔罗牌。

图片处理独立为 `yiyin.image_processing` 功能，取代原来的“对称”开关；迁移时沿用各群旧状态。静图和动图共用同一队列、流式下载、子进程硬预算和超时控制。动图保留帧序、时长、透明度与循环设置；倒放同时倒置时长，加速默认 2 倍、减速 0.5 倍。连续旋转使用固定透明画布避免裁切，静图约 2 秒一圈，动图按完整输入循环衔接，最多 100 帧、源图最大边缩至 512 像素。新模块接管第三方的同名指令，避免禁用后从旧入口绕过功能开关。

表情预览、列表、统计图、图片操作、食物拼图和 emojimix 当前具有接单前检查及系统进程组保护，尚未全部迁移内部生成到可终止子进程。接单检查不能替代执行期间的资源隔离。伪造消息仅发送转发消息，不参与图片接单检查。

自动食物收集是后台功能：资源不足时在下载前静默跳过，不向群聊发送资源错误。下载与图片任务共用并发槽，流式检查资源并限制图片体积为 8 MiB 与输入预算的较小者。所有群的开关及默认值可保持关闭，未来手动启用时仍应用这些保护。

第三方表情插件升级后，应检查 integrations.py 使用的共享 process 函数签名与消息接口。启动错误会出现在系统服务日志中。

测试代码不随项目保留；验证使用临时进程和生成图片，不发 QQ 消息。
