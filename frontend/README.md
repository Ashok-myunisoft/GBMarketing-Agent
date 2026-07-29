# GB Marketing Agent frontend

This is a standalone React/Vite frontend. It calls the FastAPI job API through Vite's local `/api` proxy.

The backend keeps its existing synchronous `POST /Ask` endpoint. The UI uses the additional asynchronous endpoints below so it can show workflow progress and history:

- `POST /jobs` — create a background lead-generation job
- `GET /jobs` and `GET /jobs/{id}` — list and inspect jobs
- `GET /jobs/{id}/events` — workflow events for the live-log panel
- `GET /jobs/{id}/leads` — saved lead results
- `GET /jobs/{id}/export` — download the generated Excel file

## Run locally

1. Install Node.js 20 or newer.
2. From this directory, run `npm install`.
3. Start the FastAPI application from the repository root: `venv\\Scripts\\python.exe backend\\main.py`.
4. In another terminal in this directory, run `npm run dev`.
5. Open `http://localhost:5173`.

The Vite proxy expects the API at `http://127.0.0.1:8040`.
