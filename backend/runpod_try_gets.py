import logging
from core.config import settings
from services.runpod_client import RunPodClient

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

payload = {
    "model": "gpt-4o-mini",
    "temperature": 0.0,
    "max_tokens": 20,
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ],
}
headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}
client = RunPodClient(base_url=settings.OPENAI_API_BASE_URL.rstrip("/"), mode="sync", logger=logger)
post_response = client.post(payload=payload, headers=headers, timeout=float(settings.OPENAI_TIMEOUT_SECONDS))
post_response.raise_for_status()
post_data = post_response.json()
print("POST returned:", post_data)
run_id = post_data.get("id") or post_data.get("run_id") or post_data.get("run_key")
print("run_id:", run_id)
if run_id:
    status_response = client.get_status(run_id, headers=headers, timeout=10.0)
    print("status response:", status_response.status_code)
    print(status_response.text[:1000])
