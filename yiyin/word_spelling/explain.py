"""
拼字解释辅助模块
- 仅供 /dqxm 生成的词语调用
- 使用 gpt-4o 将词语强行当成成语进行一本正经的简短解释
"""

from yiyin.llmapi import chat_completion

_SYSTEM_PROMPT = (
    "你是一本荒诞词典的编者，擅长一本正经地胡说八道。"
    "你的任务是把用户给出的词语当成真实存在的成语或熟语来解释。"
    "无论它看起来多么像生造词，都必须直接当真，不要说它不存在、不要说你在编、"
    "不要出现可能、也许、像是、仿佛、疑似、杜撰、虚构、编造等表述。"
    "语言风格要认真、自然、像真的辞书释义，但要简短。"
)


async def explain_dqxm_word(word: str) -> str | None:
    """把 dqxm 生成的词语交给 LLM 做简短解释。"""
    cleaned = word.strip()
    if not cleaned:
        return None

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"请把“{cleaned}”当作一个真实的四字成语来解释。\n"
                "要求：\n"
                "1. 只输出 2 句话。\n"
                "2. 第一句解释字面义和引申义。\n"
                "3. 第二句补一个很短的典故、场景或使用语境。\n"
                "4. 总长度尽量控制在 80 字以内。\n"
                "5. 不要分点，不要加书名号或引号，不要解释你自己在编。"
            ),
        },
    ]
    result = await chat_completion(
        messages,
        model="gpt-4o",
        temperature=1.0,
        max_tokens=120,
    )
    return result.strip() if result else None
