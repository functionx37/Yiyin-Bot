"""Project-owned integration; third-party installed files remain untouched."""
from functools import wraps
from inspect import getclosurevars, isfunction
import httpx

from . import ImageJob, ResourceBusy, gate


# These handlers still generate images in the Bot process. ImageJob callers
# perform admission at the actual rendering entry point instead.
_IMAGE_HANDLERS = {
    ('yiyin.food', 'handle_feast'),
    ('nonebot_plugin_memes.matchers.help', '_'),
    ('nonebot_plugin_memes.matchers.info', '_'),
    ('nonebot_plugin_memes.matchers.statistics', '_'),
    ('nonebot_plugin_memes.matchers.image_operations', 'create_matcher.<locals>._'),
    ('nonebot_plugin_emojimix', '_'),
}


def needs_image_admission(matcher):
    """Select drawing handlers, never an entire business plugin namespace."""
    for handler in matcher.handlers:
        call = handler.call
        if (getattr(call, '__module__', ''), getattr(call, '__qualname__', '')) in _IMAGE_HANDLERS:
            return True
    return False


def remove_image_command_conflicts():
    """Let the unified feature own its command names, even while disabled."""
    from nonebot.matcher import matchers
    from nonebot_plugin_memes.matchers import image_operations
    names = {'对称', '倒放', 'gif倒放', '加速', '减速', '翻转', '转', '旋转'}
    removed = 0
    for entries in list(matchers.values()):
        for matcher in list(entries):
            for handler in matcher.handlers:
                call = handler.call
                if not isfunction(call):
                    continue
                if call.__module__ not in {'nonebot_plugin_memes.matchers.command',
                                           'nonebot_plugin_memes.matchers.image_operations'}:
                    continue
                captured = getclosurevars(call).nonlocals
                command = captured.get('command')
                meme = captured.get('meme')
                keywords = command.keywords if command is not None else meme.info.keywords if meme is not None else ()
                if names.intersection(keywords):
                    matcher.destroy()
                    removed += 1
                    break
    image_operations.commands[:] = [command for command in image_operations.commands
                                     if not names.intersection(command.keywords)]
    return removed


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
    remove_image_command_conflicts()

    # Admission applies only to known drawing handlers, including preview and collage.
    # Their system cgroup remains the last-resort protection during execution.
    from nonebot.message import run_preprocessor
    from nonebot.exception import IgnoredException
    @run_preprocessor
    async def check_image_admission(matcher):
        if needs_image_admission(matcher):
            try: gate.budget()
            except ResourceBusy as error:
                await matcher.send(str(error))
                raise IgnoredException(str(error))
