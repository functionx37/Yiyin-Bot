"""图片处理：对称、动图倒放/变速、翻转、固定角度转向和连续旋转。"""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from resource_guard import ImageJob, ResourceBusy

COMMANDS = {
    '对称': ('symmetric', '左', {'左', '右', '上', '下'}),
    '倒放': ('reverse', '', {''}),
    '加速': ('speed_up', '', {''}),
    '减速': ('slow_down', '', {''}),
    '翻转': ('flip', '左右', {'左右', '上下'}),
    '转': ('turn', '90', {'90', '180', '270'}),
    '旋转': ('spin', '顺', {'顺', '逆'}),
}
HELP_TEXT = '''图片处理（附带图片，或回复一张图片后发送指令）：
/对称 [左/右/上/下]：默认左；支持静图和动图
/倒放：动图倒放（也可使用 /gif倒放）
/加速：动图 2 倍速
/减速：动图 0.5 倍速
/翻转 [左右/上下]：默认左右；支持静图和动图
/转 [90/180/270]：默认顺时针 90 度；支持静图和动图
/旋转 [顺/逆]：默认顺时针连续旋转，输出动图
静图旋转约 2 秒一圈；动图按完整播放循环衔接旋转。
群功能开关：/启用 图片处理 或 /禁用 图片处理'''


def parse_parameter(command, text):
    action, default, allowed = COMMANDS[command]
    parameter = text.strip() or default
    if parameter not in allowed:
        choices = '/'.join(sorted(allowed))
        hint = f'可选参数：{choices}' if choices else '这个指令不需要参数'
        raise ValueError(f'/{command} 参数不正确，{hint}。发送 /图片处理 查看用法')
    return action, parameter


def _extract_image_url(message):
    return next((segment.data['url'] for segment in message
                 if segment.type == 'image' and segment.data.get('url')), None)


def _make_handler(command):
    async def handle(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()):
        try:
            action, parameter = parse_parameter(command, args.extract_plain_text())
        except ValueError as error:
            await matcher.finish(str(error))
        url = _extract_image_url(args)
        if not url and event.reply:
            url = _extract_image_url(event.reply.message)
        if not url:
            await matcher.finish(f'请附带图片或回复一张图片，例如：/{command} [图片]\n发送 /图片处理 查看完整用法')
        try:
            async with ImageJob() as job:
                source = await job.download(url)
                output = await job.render('image_processing', {
                    'source': source, 'action': action, 'parameter': parameter,
                })
                await matcher.send(MessageSegment.image(output.as_uri()))
        except ResourceBusy as error:
            await matcher.finish(str(error))
        except Exception:
            logger.exception('图片处理或发送失败：{}', command)
            await matcher.finish('图片处理或发送失败，请稍后重试')
        await matcher.finish()
    return handle


process_matchers = {}
for _command in COMMANDS:
    _matcher = on_command(_command, aliases={'gif倒放'} if _command == '倒放' else set(), priority=10, block=True)
    _matcher.append_handler(_make_handler(_command))
    process_matchers[_command] = _matcher

image_help_cmd = on_command('图片处理', priority=10, block=True)


@image_help_cmd.handle()
async def handle_image_help():
    await image_help_cmd.finish(HELP_TEXT)
