"""Project-owned integration; third-party installed files remain untouched."""
from functools import wraps
import httpx

from . import ImageJob, ResourceBusy, gate


def install():
    import nonebot_plugin_memes.matchers.command as command
    if getattr(command.process,'_resource_guard',False):return

    @wraps(command.process)
    async def guarded_process(bot,event,state,matcher,session,meme,images,texts,options=None,show_info=False):
        from nonebot_plugin_alconna import UniMessage
        from nonebot.exception import AdapterException
        try:
            async with ImageJob() as job:
                inputs=[]
                for image in images:
                    gate.budget()
                    # Common HTTP inputs stream to disk, avoiding unbounded image_fetch.
                    if image.url and image.url.startswith(('http://','https://')):
                        path=await job.download(image.url)
                    else:
                        result=await command.image_fetch(event,bot,state,image)
                        if not isinstance(result,bytes):raise NotImplementedError
                        path=job.add_bytes(result)
                        del result
                    inputs.append({'name':image.name,'path':str(path)})
                output=await job.render('meme',{'key':meme.key,'images':inputs,'texts':texts,'options':options or {}})
                await command.record_meme_generation(session,meme.key)
                message=UniMessage()
                if show_info:
                    message+= '关键词：'+'、'.join(f'"{word}"' for word in meme.info.keywords)
                message+=UniMessage.image(path=output)
                await message.finish()
        except ResourceBusy as error:
            await matcher.finish(str(error))
        except NotImplementedError:
            await matcher.finish('当前平台可能不支持下载图片')
        except AdapterException:
            await matcher.finish('图片下载或发送失败，请稍后重试')
        except httpx.HTTPError:
            await matcher.finish('图片下载失败，请稍后重试')
    guarded_process._resource_guard=True
    command.process=guarded_process

    # Admission applies to other image handlers too, including preview and collage.
    # Their system cgroup remains the last-resort protection during execution.
    from nonebot.message import run_preprocessor
    from nonebot.exception import IgnoredException
    @run_preprocessor
    async def check_image_admission(matcher):
        module=getattr(matcher,'module_name','') or ''
        if module.startswith(('yiyin.meme','yiyin.quotes','yiyin.score_image','yiyin.tarot','yiyin.food',
                              'nonebot_plugin_memes','nonebot_plugin_emojimix','nonebot_plugin_fakemsg')):
            try: gate.budget()
            except ResourceBusy as error:
                await matcher.send(str(error))
                raise IgnoredException(str(error))
