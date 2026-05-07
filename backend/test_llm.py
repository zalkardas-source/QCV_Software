
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

try:
    response = client.chat.completions.create(
        model="minimax/minimax-m2.7",
        messages=[{"role": "user", "content": "Say hello"}],
        temperature=0.1
    )
    print(f"Success: {response.choices[0].message.content}")
except Exception as e:
    print(f"Error: {e}")
