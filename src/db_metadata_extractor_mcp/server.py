"""
db-metadata-extractor MCP Server (stdio transport)

Extracts database schema metadata and saves it directly to a local output folder.
Designed for use with VS Code Agent Mode, Claude Desktop, and other MCP clients.

Installable from the VS Code @mcp gallery via the GitHub MCP Registry.
"""

import os
import sys
import json
import uuid
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# ── Add reference module to path ──────────────────────────────────────────────
_REF_PATH = Path(__file__).parent / "reference" / "db_extraction_schema"
if str(_REF_PATH) not in sys.path:
    sys.path.insert(0, str(_REF_PATH))

from services import extract_raw_metadata  # noqa: E402
from mcp.server.fastmcp import FastMCP

# ── Init FastMCP ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "db-metadata-extractor",
    instructions=(
        "Extract database schema metadata (tables, columns, keys, indexes) "
        "from PostgreSQL, Snowflake, SQL Server, BigQuery, and Oracle databases. "
        "Saves the full JSON output directly to a local folder."
    ),
)


# ── Helper: generate filename & save ──────────────────────────────────────────
def _save_json(data: dict, output_dir: str, label: str) -> dict:
    """Write *data* as pretty-printed JSON into *output_dir* and return file info."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{label}_{timestamp}_{uuid.uuid4().hex[:6]}.json"
    filepath = out / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    return {
        "saved_to": str(filepath.resolve()),
        "filename": filename,
        "size_bytes": filepath.stat().st_size,
    }


# ── Tool 1: extract_metadata ─────────────────────────────────────────────────
@mcp.tool()
async def extract_metadata(
    db_type: str,
    output_path: str,
    database_name: str = "",
    host: str = "",
    port: int = 0,
    username: str = "",
    password: str = "",
    schema_name: str = "",
    tables: Optional[List[str]] = None,
    # Snowflake-specific
    account: str = "",
    warehouse: str = "",
    role_name: str = "",
    # BigQuery-specific
    project_id: str = "",
    service_account_key: str = "",
) -> dict:
    """
    Extract schema metadata from a live database and save the full JSON
    to a local folder.  Returns a lightweight summary (never the full JSON).

    The output_path MUST be an absolute directory path where the JSON file
    will be written.  If the directory does not exist it will be created.

    Args:
        db_type:             Database type — one of: 'postgres', 'snowflake',
                             'sqlserver', 'bigquery', 'oracle'
        output_path:         Absolute directory path to save the extracted JSON.
                             Example: /Users/you/project/outputs  or
                             C:\\Users\\you\\project\\outputs
        database_name:       Database / catalog name
        host:                Hostname or IP (postgres, sqlserver, oracle)
        port:                Port number (postgres=5432, sqlserver=1433,
                             oracle=1521)
        username:            Login username
        password:            Login password
        schema_name:         Schema to extract (postgres default 'public',
                             sqlserver default 'dbo')
        tables:              Optional list of table names to limit extraction
        account:             Snowflake account identifier
        warehouse:           Snowflake warehouse name
        role_name:           Snowflake role name
        project_id:          BigQuery GCP project ID
        service_account_key: BigQuery service-account JSON key (as string)

    Example — PostgreSQL:
        {
            "db_type": "postgres",
            "output_path": "/home/user/metadata_output",
            "host": "10.0.0.5",
            "port": 5432,
            "username": "admin",
            "password": "secret",
            "database_name": "mydb",
            "schema_name": "public"
        }

    Example — Oracle:
        {
            "db_type": "oracle",
            "output_path": "C:\\\\metadata_output",
            "host": "20.168.23.183",
            "port": 1521,
            "database_name": "orclpdb",
            "username": "OPT_JCT_DE",
            "password": "OPT_JCT_DE"
        }
    """

    if not output_path:
        return {"status": "error", "message": "output_path is required."}

    request_data = {
        "db_type": db_type,
        "database_name": database_name,
        "host": host or None,
        "port": port or None,
        "username": username or None,
        "password": password or None,
        "schema_name": schema_name or None,
        "tables": tables or None,
        "account": account or None,
        "warehouse": warehouse or None,
        "role_name": role_name or None,
        "project_id": project_id or None,
        "service_account_key": service_account_key or None,
    }

    try:
        metadata = extract_raw_metadata(request_data)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    # ── Compute summary stats ─────────────────────────────────────────────
    schemas = metadata.get("schemas", [])
    total_tables = sum(len(s.get("tables", [])) for s in schemas)
    total_columns = sum(
        len(t.get("columns", []))
        for s in schemas
        for t in s.get("tables", [])
    )
    table_names = [
        t.get("table_name", "?")
        for s in schemas
        for t in s.get("tables", [])
    ]

    # ── Save to disk ──────────────────────────────────────────────────────
    label = project_id or database_name or db_type
    file_info = _save_json(metadata, output_path, label)

    return {
        "status": "success",
        "summary": {
            "db_type": db_type,
            "database": database_name or project_id,
            "total_tables": total_tables,
            "total_columns": total_columns,
            "table_names_preview": table_names[:30],
            "extracted_at": metadata.get("source", {}).get(
                "extracted_at", datetime.now().isoformat()
            ),
        },
        "file": file_info,
        "hint": (
            "Full metadata saved to disk. "
            "Use the query_metadata tool to explore tables/columns "
            "without loading the entire file."
        ),
    }


# ── Tool 2: query_metadata ───────────────────────────────────────────────────
@mcp.tool()
async def query_metadata(
    filepath: str,
    table_name: str = "",
    field_name: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Query a previously-saved metadata JSON file on disk without loading
    everything into context.

    Args:
        filepath:   Absolute path to the JSON file saved by extract_metadata
        table_name: Filter by table name (substring match, optional)
        field_name: Filter by column/field name (substring match, optional)
        page:       Page number (default 1)
        page_size:  Records per page (default 20)
    """
    p = Path(filepath)
    if not p.exists():
        return {"status": "error", "message": f"File not found: {filepath}"}

    with open(p, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    tables = [
        t
        for s in metadata.get("schemas", [])
        for t in s.get("tables", [])
    ]

    if table_name:
        tables = [
            t for t in tables
            if table_name.lower() in t.get("table_name", "").lower()
        ]

    if field_name:
        tables = [
            t for t in tables
            if any(
                field_name.lower() in c.get("column_name", "").lower()
                for c in t.get("columns", [])
            )
        ]

    total = len(tables)
    start = (page - 1) * page_size
    paged = tables[start : start + page_size]

    return {
        "status": "success",
        "total_matches": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),
        "results": paged,
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────
def main():
    """CLI entrypoint — runs the MCP server over stdio."""
    parser = argparse.ArgumentParser(
        description="db-metadata-extractor MCP server"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()