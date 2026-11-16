
import os
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
    return {
        "status": "ok",
        "service": "opspilot-api",
        "report_service_configured": bool(
            REPORT_SERVICE_URL and REPORT_API_TOKEN
        ),
    }


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
