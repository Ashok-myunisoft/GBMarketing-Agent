import traceback
from services.llm_services import LLMService

try:
    svc = LLMService()
    res = svc.invoke('You are a helpful assistant.', 'Say hello and your name.', temperature=0.0, max_tokens=60)
    print('RESULT:\n', res)
except Exception as e:
    print('ERROR:', type(e).__name__, str(e))
    traceback.print_exc()
