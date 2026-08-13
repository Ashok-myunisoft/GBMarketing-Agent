import sys
types = __import__('types')
from pathlib import Path

# Add backend to sys.path so imports resolve as in normal execution.
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

# Fake dependencies that the project normally installs.
playwright = types.ModuleType('playwright')
playwright_sync = types.ModuleType('playwright.sync_api')
playwright_sync.sync_playwright = lambda: None
playwright_sync.Playwright = object
playwright_sync.Browser = object
playwright_sync.BrowserContext = object
playwright_sync.Page = object
playwright_sync.Error = Exception
playwright_sync.TimeoutError = Exception
sys.modules['playwright'] = playwright
sys.modules['playwright.sync_api'] = playwright_sync

httpx = types.ModuleType('httpx')
httpx.RequestError = Exception
httpx.HTTPStatusError = Exception
sys.modules['httpx'] = httpx

services_llm = types.ModuleType('services.llm_services')
class LLMService:
    def __init__(self):
        pass
    def invoke(self, *args, **kwargs):
        return '{"selected_index": 0}'
services_llm.LLMService = LLMService
sys.modules['services.llm_services'] = services_llm

services_prompt = types.ModuleType('services.prompt_service')
class PromptService:
    @staticmethod
    def load(name):
        return 'prompt'
services_prompt.PromptService = PromptService
sys.modules['services.prompt_service'] = services_prompt

# Import the service and patch behaviour.
import services.gst_turnover_enrichment.service as svc_mod
import services.gst_turnover_enrichment.website_source as website_source
import services.gst_turnover_enrichment.directory_source as directory_source

website_source.collect = lambda browser, website: ([], [])
directory_source.collect = lambda browser, company_name: ([], [])
svc_mod.google_fallback.collect = lambda browser, company_name: ([], False)

class FakeGstTurnoverService:
    def __init__(self, browser):
        self.browser = browser
    def lookup(self, gstin):
        print('jamku_lookup_called', gstin)
        return 'Rs. 5 Cr. to 25 Cr.'

svc_mod.GstTurnoverService = FakeGstTurnoverService

service = svc_mod.GstTurnoverEnrichmentService(object())
result = service.resolve('Test Company', 'http://example.com', gst='27AAACI1503H1ZN')
print('GST result:', result.gst.value, result.gst.confidence, result.gst.sources)
print('Turnover result:', result.turnover.value, result.turnover.confidence, result.turnover.sources)
