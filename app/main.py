
import os
import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .tools import validate_local_service_url

# Load the root project .env regardless of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from .agent import run_agent

app = FastAPI(
    title="OpsPilot AI",
    version="1.0.0",
    description="AI-assisted IT incident diagnostics and PDF reports.",
)

STATIC_DIR = PROJECT_ROOT / "app" / "static"
REPORT_SERVICE_URL = os.getenv(
    "REPORT_SERVICE_URL", "http://127.0.0.1:3001"
).rstrip("/")
REPORT_API_TOKEN = os.getenv("REPORT_API_TOKEN", "")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "opspilot.db")))
logger = logging.getLogger("opspilot")
REQUEST_COUNT = 0
REQUEST_ERRORS = 0


def _initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, incident TEXT NOT NULL,
            diagnosis TEXT NOT NULL
        )""")


@app.middleware("http")
async def observe_requests(request, call_next):
    global REQUEST_COUNT, REQUEST_ERRORS
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id) else str(uuid.uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        REQUEST_ERRORS += 1
        logger.exception("request_failed request_id=%s path=%s", request_id, request.url.path)
        raise
    REQUEST_COUNT += 1
    REQUEST_ERRORS += int(response.status_code >= 500)
    response.headers["X-Request-ID"] = request_id
    logger.info("request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id, request.method, request.url.path, response.status_code,
                (time.perf_counter() - started) * 1000)
    return response

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)


class DiagnoseRequest(BaseModel):
    incident: str = Field(min_length=3, max_length=4000)
    logs: str = Field(default="", max_length=12000)
    health_url: str | None = Field(default=None, max_length=2048)


class ReportRequest(BaseModel):
    incident: str = Field(min_length=3, max_length=4000)
    summary: str = Field(min_length=1, max_length=6000)
    severity: str = Field(default="Unknown", max_length=50)
    confidence: str = Field(default="Not specified", max_length=50)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    model: str = Field(default="Not specified", max_length=150)


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    _initialize_database()
    return {
        "status": "ok",
        "service": "opspilot-api",
        "report_service_configured": bool(
            REPORT_SERVICE_URL and REPORT_API_TOKEN
        ),
    }


@app.get("/api/ready")
async def readiness():
    try:
        _initialize_database()
        with sqlite3.connect(DATABASE_PATH, timeout=1) as connection:
            connection.execute("SELECT 1")
        return {"status": "ready", "database": "ok"}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(
        f"# HELP opspilot_http_requests_total HTTP requests handled\n# TYPE opspilot_http_requests_total counter\nopspilot_http_requests_total {REQUEST_COUNT}\n# HELP opspilot_http_errors_total HTTP 5xx responses\n# TYPE opspilot_http_errors_total counter\nopspilot_http_errors_total {REQUEST_ERRORS}\n",
        media_type="text/plain; version=0.0.4",
    )


@app.get("/api/incidents")
async def incident_history(limit: int = 20):
    _initialize_database()
    limit = max(1, min(limit, 100))
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, created_at, incident, diagnosis FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{**dict(row), "diagnosis": json.loads(row["diagnosis"])} for row in rows]


@app.post("/api/diagnose")
async def diagnose(payload: DiagnoseRequest):
    if payload.health_url:
        validation_error = validate_local_service_url(payload.health_url)
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

    try:
        result = await run_agent(
            payload.incident,
            payload.logs,
            payload.health_url,
        )

        if not isinstance(result, dict):
            raise ValueError(
                "run_agent() must return a dictionary."
            )

        result.setdefault("summary", "No summary returned by the agent.")
        result.setdefault("severity", "Unknown")
        result.setdefault("confidence", "Not specified")
        result.setdefault("evidence", [])
        result.setdefault("recommendations", [])
        result["incident"] = payload.incident
        _initialize_database()
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                "INSERT INTO incidents (id, created_at, incident, diagnosis) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 payload.incident, json.dumps(result, ensure_ascii=False)),
            )

        health_check = next(
            (
                entry["result"]
                for entry in result.get("tool_trace", [])
                if entry.get("tool") == "check_local_service"
            ),
            None,
        )
        return {"diagnosis": result, "health_check": health_check}

    except HTTPException:
        raise
    except Exception as exc:
        # Do not return stack traces or secret configuration to the browser.
        print(f"Diagnosis failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Diagnosis failed. Check the API server logs.",
        ) from exc


@app.post("/api/reports")
async def create_report(payload: ReportRequest):
    if not REPORT_API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="PDF service is not configured. Set REPORT_API_TOKEN in .env.",
        )

    report_payload = payload.model_dump()

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            upstream = await client.post(
                f"{REPORT_SERVICE_URL}/reports",
                json=report_payload,
                headers={
                    "Authorization": f"Bearer {REPORT_API_TOKEN}",
                },
            )
    except httpx.RequestError as exc:
        print(f"Report service connection failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to the PDF report service.",
        ) from exc

    if upstream.status_code != 200:
        print(
            "Report service returned status:",
            upstream.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="The PDF report service could not generate the report.",
        )

    content_type = upstream.headers.get(
        "content-type", ""
    ).lower()

    if "application/pdf" not in content_type:
        raise HTTPException(
            status_code=502,
            detail="The report service returned an invalid file type.",
        )

    return Response(
        content=upstream.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="opspilot-report.pdf"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=4000)
