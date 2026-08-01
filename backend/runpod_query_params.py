import httpx
from core.config import settings

with httpx.Client(timeout=float(settings.OPENAI_TIMEOUT_SECONDS)) as c:
    post = c.post(settings.OPENAI_API_BASE_URL, json={"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}, headers={"Authorization":f"Bearer {settings.OPENAI_API_KEY}","Content-Type":"application/json"})
    post.raise_for_status()
    d = post.json()
print('post', d)
run_id = d.get('id')
for q in ('id','run_id','runKey','run_key','key'):
    url = settings.OPENAI_API_BASE_URL + '?' + q + '=' + run_id
    try:
        r = httpx.get(url, headers={"Authorization":f"Bearer {settings.OPENAI_API_KEY}"}, timeout=10.0)
        print('GET', url, r.status_code)
        try:
            print(r.json())
        except Exception:
            print(r.text[:1000])
    except Exception as e:
        print('err', e)
