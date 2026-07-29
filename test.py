import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.browser_service import BrowserService


if __name__ == "__main__":
    browser = BrowserService()
    html = browser.get_page_content("https://www.google.com")
    print(html[:1000])
