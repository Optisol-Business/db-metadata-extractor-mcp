import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from models import ExtractRequest, EnrichRequest, ReportRequest
from services import extract_raw_metadata, enrich_metadata_with_ai
from generate_schema_report import generate_report


app = FastAPI(
    title="Metadata Extraction API",
    description="API to extract database metadata, enrich with AI descriptions, and generate HTML reports",
    version="2.0.0",
)

# ── Hardcoded JSON file path ──────────────────────────────────────────────
METADATA_FILE = os.path.join(os.path.dirname(__file__), "output", "metadata_store.json")


def _read_store() -> list:
    """Read the JSON array from the metadata store file."""
    if not os.path.exists(METADATA_FILE):
        return []
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_store(data: list) -> None:
    """Write the JSON array back to the metadata store file."""
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _find_entry(store: list, system_name: str, user_id: str | None = None, owner_name: str | None = None) -> dict | None:
    """Find an entry in the store by system_name and optionally user_id and owner_name."""
    for entry in store:
        match = entry.get("system_name") == system_name
        if match and user_id:
            match = entry.get("user_id") == user_id
        if match and owner_name:
            match = entry.get("owner_name") == owner_name
        if match:
            return entry
    return None


# ── Endpoint 1: Raw Metadata Extraction ───────────────────────────────────

@app.post("/api/metadata/extract")
async def extract_metadata_endpoint(request: ExtractRequest):
    """
    Extracts raw database metadata (no AI descriptions).
    Appends the result to the local JSON file under the 'metadata' key.
    """
    try:
        # Extract raw metadata from the database
        raw_metadata = extract_raw_metadata(request.dict())

        # Read existing store
        store = _read_store()

        # Check if system_name already exists — update it
        existing = _find_entry(store, request.system_name)
        if existing:
            existing["metadata"] = raw_metadata
            existing["added_at"] = datetime.now(timezone.utc).isoformat()
        else:
            # Append new entry with wrapper fields
            entry = {
                "system_name": request.system_name,
                "owner_name": request.owner_name,
                "user_id": request.user_id,
                "source_type": request.db_type,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "metadata": raw_metadata,
            }
            store.append(entry)

        # Save back to file
        _write_store(store)

        return {
            "message": f"Metadata extracted for '{request.system_name}'",
            "file_path": METADATA_FILE,
            "data": raw_metadata,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Endpoint 2: AI Description Enrichment ─────────────────────────────────

@app.post("/api/metadata/enrich")
async def enrich_metadata_endpoint(request: EnrichRequest):
    """
    Reads raw metadata from the JSON file for the given system_name,
    generates AI descriptions, and updates the file IN-PLACE.
    """
    try:
        store = _read_store()
        entry = _find_entry(store, request.system_name, request.user_id)

        if not entry:
            raise HTTPException(
                status_code=404,
                detail=f"System '{request.system_name}' not found in metadata store",
            )

        metadata = entry.get("metadata")
        if not metadata:
            raise HTTPException(
                status_code=400,
                detail=f"No metadata found for system '{request.system_name}'. Run /extract first.",
            )

        # Enrich metadata with AI descriptions IN-PLACE
        enriched = enrich_metadata_with_ai(metadata)
        entry["metadata"] = enriched

        # Save back to file
        _write_store(store)

        return {
            "message": f"AI descriptions generated for '{request.system_name}'",
            "file_path": METADATA_FILE,
            "data": enriched,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Endpoint 3: HTML Report Generation ────────────────────────────────────

@app.post("/api/metadata/report", response_class=HTMLResponse)
async def generate_report_endpoint(request: ReportRequest):
    """
    Reads metadata from the JSON file for the given system_name
    and returns an interactive HTML schema report.
    """
    try:
        store = _read_store()
        entry = _find_entry(store, request.system_name, request.user_id)

        if not entry:
            raise HTTPException(
                status_code=404,
                detail=f"System '{request.system_name}' not found in metadata store",
            )

        metadata = entry.get("metadata")
        if not metadata:
            raise HTTPException(
                status_code=400,
                detail=f"No metadata found for system '{request.system_name}'. Run /extract first.",
            )

        html = generate_report(metadata)
        return HTMLResponse(content=html)

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)