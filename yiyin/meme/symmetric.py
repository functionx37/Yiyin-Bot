"""对称图片使用与其他图片指令共用的资源保护。"""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot,MessageEvent,Message,MessageSegment
from nonebot.params import CommandArg
from nonebot.log import logger
from resource_guard import ImageJob,ResourceBusy

symmetric_cmd=on_command('对称',priority=10,block=True)
VALID_DIRECTIONS={'左','右','上','下'}


def _extract_image_url(message):
    return next((segment.data['url'] for segment in message if segment.type=='image' and segment.data.get('url')),None)


@symmetric_cmd.handle()
async def handle_symmetric(bot:Bot,event:MessageEvent,args:Message=CommandArg()):
    text=args.extract_plain_text().strip()
    direction=text[0] if text and text[0] in VALID_DIRECTIONS else '左'
    url=_extract_image_url(args)
    if not url and event.reply:url=_extract_image_url(event.reply.message)
    if not url:await symmetric_cmd.finish('请附带图片或回复一张图片，例如：/对称 左 [图片]')
    try:
        async with ImageJob() as job:
            source=await job.download(url)
            output=await job.render('symmetric',{'source':source,'direction':direction})
            await symmetric_cmd.send(MessageSegment.image(output.as_uri()))
    except ResourceBusy as error:
        await symmetric_cmd.finish(str(error))
    except Exception:
        logger.exception('对称图片处理或发送失败')
        await symmetric_cmd.finish('图片处理或发送失败，请稍后重试')
    await symmetric_cmd.finish()
