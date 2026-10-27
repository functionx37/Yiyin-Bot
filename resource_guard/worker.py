"""Standalone trusted-operation dispatcher; never imports bot plugins."""
import json
import resource
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def decode(value):
    if isinstance(value,dict):
        if set(value)=={'__bytes_file__'}: return Path(value['__bytes_file__']).read_bytes()
        return {key:decode(item) for key,item in value.items()}
    if isinstance(value,list): return [decode(item) for item in value]
    return value


def execute(operation, payload, budget):
    if operation=='image_processing':
        from resource_guard.renderers.image_processing import process_image
        return process_image(payload['source'],payload['action'],payload['parameter'],budget)
    if operation=='symmetric':
        from PIL import Image
        from resource_guard.renderers.symmetric import _process_animated,_process_static,estimate_memory
        source=Path(payload['source'])
        with Image.open(source) as image:
            if estimate_memory(*image.size,getattr(image,'n_frames',1),source.stat().st_size)>budget:
                raise ValueError('这张图片预计需要的内存超过当前可用额度，请稍后重试')
            return (_process_animated if getattr(image,'is_animated',False) else _process_static)(image,payload['direction'])
    if operation=='strong':
        from resource_guard.renderers.ziming import _draw_bibi
        return _draw_bibi(payload['texts'])
    if operation=='think':
        from resource_guard.renderers.think import _draw_template,_TemplateConfig
        template=payload['template']
        template['image_path']=Path(template['image_path'])
        return _draw_template(payload['text'],_TemplateConfig(**template))
    if operation=='salt':
        from resource_guard.renderers.salt import _draw_salt
        return _draw_salt(payload['text'])
    if operation=='avatar':
        from resource_guard.renderers.xnn import _composite_xnn
        return _composite_xnn(payload['avatar_bytes'])
    if operation=='chat':
        from resource_guard.renderers.chat import generate_chat_screenshot
        return generate_chat_screenshot(**payload)
    if operation in ('grayscale','tarot'):
        from PIL import Image
        from io import BytesIO
        source=BytesIO(payload['image_bytes']) if operation=='grayscale' else payload['path']
        with Image.open(source) as image:
            image=image.convert('L') if operation=='grayscale' else image.copy()
            if operation=='tarot' and not payload['upright']:image=image.rotate(180)
            with image:
                output=BytesIO()
                image.save(output,format='PNG')
                return output.getvalue()
    if operation=='meme':
        from meme_generator import get_meme,Image
        images=[Image(item['name'],Path(item['path']).read_bytes()) for item in payload['images']]
        result=get_meme(payload['key']).generate(images,payload['texts'],payload['options'])
        if isinstance(result,bytes):return result
        kind=type(result).__name__
        if kind=='MemeFeedback':raise ValueError(result.feedback)
        if kind=='TextOverLength':raise ValueError('文字过长')
        if kind.endswith('Mismatch'):raise ValueError(f'图片或文字数量不符，应为 {result.min}~{result.max}，实际 {result.actual}')
        detail=getattr(result,'error',getattr(result,'path',kind))
        raise ValueError(f'表情生成失败：{detail}')
    raise ValueError('未知图片任务')


def main():
    request,output,budget_text=sys.argv[1:]
    budget=int(budget_text)
    resource.setrlimit(resource.RLIMIT_AS,(budget,budget))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    try:
        job=json.loads(Path(request).read_text())
        result=execute(job['operation'],decode(job['payload']),budget)
        Path(output).write_bytes(result)
    except (MemoryError,OSError,ValueError) as error:
        print('图片任务超过当前内存预算，已安全终止' if isinstance(error,MemoryError) else str(error)[:300],file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
