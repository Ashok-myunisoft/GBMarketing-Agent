import sys
from pathlib import Path

# Support both `python backend/main.py` and `uvicorn backend.main:app` from
# the repository root without requiring callers to set PYTHONPATH.
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI
from api.routers import router

app = FastAPI(title="GBMarketing-Agent")

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8040)


