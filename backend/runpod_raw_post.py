import json
import os
import httpx
from core.config import settings

payload = {
    "model": "gpt-4o-mini",
    "temperature": 0.0,
    "max_tokens": 20,
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"}
    ]
}
headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}
print('POST', settings.OPENAI_API_BASE_URL)
with httpx.Client(timeout=float(settings.OPENAI_TIMEOUT_SECONDS)) as client:
    resp = client.post(settings.OPENAI_API_BASE_URL, json=payload, headers=headers)
    print('status_code =', resp.status_code)
    try:
        print('json =', json.dumps(resp.json(), indent=2))
    except Exception:
        print('text =', resp.text)
    print('headers =', dict(resp.headers))
