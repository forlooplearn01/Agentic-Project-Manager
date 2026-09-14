import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Single shared OpenAI client - reused across all pipeline LLM calls
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def call_llm(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        seed=42,
    )
    return response.choices[0].message.content


def call_llm_json(prompt: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        seed=42,
    )

    raw_text = response.choices[0].message.content.strip()

    return json.loads(raw_text)
