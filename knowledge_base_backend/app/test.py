from openai import OpenAI
from app.config import MINIMAX_MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL


class MinimaxConfigError(RuntimeError):
    pass

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

print("Starting stream response...\n")
print("=" * 60)
print("Thinking Process:")
print("=" * 60)

stream = client.chat.completions.create(
    model="MiniMax-M2.5",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "介绍下python"},
    ],
    # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
    extra_body={"reasoning_split": False},
    stream=True,
)

reasoning_buffer = ""
text_buffer = ""

for chunk in stream:
    if (
        hasattr(chunk.choices[0].delta, "reasoning_details")
        and chunk.choices[0].delta.reasoning_details
    ):
        for detail in chunk.choices[0].delta.reasoning_details:
            if "text" in detail:
                reasoning_text = detail["text"]
                new_reasoning = reasoning_text[len(reasoning_buffer) :]
                if new_reasoning:
                    print(new_reasoning, end="", flush=True)
                    reasoning_buffer = reasoning_text

    if chunk.choices[0].delta.content:
        content_text = chunk.choices[0].delta.content
        new_text = content_text[len(text_buffer) :] if text_buffer else content_text
        if new_text:
            print(new_text, end="", flush=True)
            text_buffer = content_text

print("\n" + "=" * 60)
print("Response Content:")
print("=" * 60)
print(f"{text_buffer}\n")