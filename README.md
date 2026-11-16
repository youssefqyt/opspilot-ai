# OpsPilot AI

OpsPilot is an evidence-first incident triage assistant. It accepts an incident and sanitized logs, can inspect host resource usage and an explicitly supplied localhost health endpoint, and returns a severity, evidence, confidence, and operator recommendations. It includes a PDF report service and a persistent local incident history.

## Capabilities

- Bounded LLM tool loop with an explicit allowlist; no shell execution.
- Log pattern analysis for database, timeout, authentication, HTTP, memory, and network failures.
- Restricted HTTP health checks for `localhost` and `127.0.0.1` only.
- Read-only host CPU, memory, disk, OS, and Python metrics.
- SQLite incident history at `GET /api/incidents?limit=20` (maximum 100).
- API liveness (`GET /api/health`), readiness (`GET /api/ready`), request ID and structured request logs, and Prometheus text metrics (`GET /metrics`).
- Authenticated PDF report generation via the separate Node service.
- Docker Compose deployment with persistent storage and health checks; GitHub Actions CI for Python, Node, and Compose configuration.

## Quick start with Docker Compose

1. Copy `.env.example` to `.env` and set a valid `LLM_API_KEY` for your chosen provider. Generate a unique report token (for example, `python -c "import secrets; print(secrets.token_urlsafe(48))"`) and set `REPORT_API_TOKEN`.
2. Start both services: `docker compose up --build`.
3. Open [http://localhost:4000](http://localhost:4000). API docs are at `/docs`; readiness is at `/api/ready`.

The API is published on port 4000; the report service stays on the internal Compose network. SQLite data persists in the `opspilot-data` volume. Back up that volume to preserve incident history. For production, terminate TLS at a trusted reverse proxy and restrict access to the dashboard and metrics endpoint.

## Run locally

Requires Python 3.10+ and Node.js 20+. Create a virtual environment, install `requirements.txt`, run `npm ci --prefix report_service`, and configure `.env` as above. In separate terminals:

```bash
npm --prefix report_service start
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 4000
```

The Python API uses `REPORT_SERVICE_URL` (default `http://127.0.0.1:3001`). Set `DATABASE_PATH` to relocate the SQLite database. Provider-compatible settings are `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness and report configuration status |
| GET | `/api/ready` | Readiness including SQLite connectivity |
| GET | `/metrics` | Prometheus request and 5xx counters |
| GET | `/api/incidents?limit=20` | Recent persisted diagnoses |
| POST | `/api/diagnose` | Diagnose `{incident, logs?, health_url?}` |
| POST | `/api/reports` | Generate a PDF from diagnosis fields |

Diagnostics send incident text and logs to the configured LLM provider. Sanitize logs and remove credentials and personal data before submission. Recommendations are advisory; operators must validate them. The assistant does not execute remediation. The history database contains submitted incident text and results, so protect and retain it according to your organization's policies.

## Development

```bash
pip install -r requirements.txt
pytest -q
npm ci --prefix report_service
node --check report_service/index.js
```
