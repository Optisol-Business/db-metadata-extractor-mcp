from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict

class MetadataWithCredentialsRequest(BaseModel):
    db_type: str
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    schema_name: Optional[str] = None
    # Snowflake specific
    account: Optional[str] = None
    warehouse: Optional[str] = None
    role_name: Optional[str] = None
    # BigQuery specific
    project_id: Optional[str] = None
    service_account_key: Optional[str] = None
    # Table selection
    tables: Optional[List[str]] = None


# ── New request models for the 3-endpoint API ──────────────────────────────

class ExtractRequest(MetadataWithCredentialsRequest):
    """Endpoint 1: raw metadata extraction."""
    system_name: str
    owner_name: str
    user_id: str


class EnrichRequest(BaseModel):
    """Endpoint 2: AI description enrichment."""
    user_id: str
    owner_name: str
    system_name: str


class ReportRequest(BaseModel):
    """Endpoint 3: HTML report generation."""
    user_id: str
    owner_name: str
    system_name: str


# ── Metadata response models ───────────────────────────────────────────────

class ColumnMetadata(BaseModel):
    column_name: str
    data_type: str
    nullable: bool
    unique: bool = False
    is_generated: bool = False
    primary_key: bool = False
    foreign_key: bool = False
    distinct_count: Optional[int] = None
    references: Optional[Dict[str, str]] = None
    description: Optional[str] = None

class TableMetadata(BaseModel):
    table_name: str
    table_type: str
    row_count: Optional[int] = None
    size: Optional[str] = None
    columns: List[ColumnMetadata]
    description: Optional[str] = None

class SchemaMetadata(BaseModel):
    schema_name: str
    tables: List[TableMetadata]

class SourceMetadata(BaseModel):
    db_type: str
    database: Optional[str] = None
    schema_name: str = Field(..., alias="schema")
    extracted_at: str
    version: str = "Version_1"
    
    class Config:
        populate_by_name = True

class MetadataResponse(BaseModel):
    source: SourceMetadata
    schemas: List[SchemaMetadata]
