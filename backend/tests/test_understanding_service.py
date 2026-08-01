import unittest
from unittest.mock import Mock

from services.understanding_service import UnderstandingService


class UnderstandingServiceTests(unittest.TestCase):
    def test_accepts_json_wrapped_in_a_markdown_fence(self):
        service = UnderstandingService.__new__(UnderstandingService)
        service.prompt_service = Mock()
        service.prompt_service.load.return_value = "prompt"
        service.llm = Mock()
        service.llm.invoke.return_value = '''```json
{"intent":"lead_generation","industry":"Valve","location":"Hyderabad","buyer_persona":"","workflow":"lead_generation","confidence":0.98}
```'''

        result = service.understand("list valve industries in Hyderabad")

        self.assertEqual(result.industry, "Valve")
        self.assertEqual(result.workflow, "lead_generation")


if __name__ == "__main__":
    unittest.main()
