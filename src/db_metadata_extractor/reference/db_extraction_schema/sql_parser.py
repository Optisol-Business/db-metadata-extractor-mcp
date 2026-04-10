"""
SQL DDL Parser — Hybrid (Python Pre-filter + LLM)
==================================================
Parses .sql DDL files to extract database schema metadata.

Strategy (optimised for token cost):
  1. Python pre-filter  — strip comments, DML (INSERT/UPDATE/DELETE), SET,
     USE, GO, and other noise.  Only CREATE TABLE / ALTER TABLE statements
     are kept.  This reduces a 50K-token file to ~2-5K tokens.
  2. LLM (AWS Bedrock)  — the condensed DDL is sent to Claude with a
     structured JSON prompt.  The LLM returns the exact metadata shape
     needed by the rest of the pipeline.
  3. Fallback           — if Bedrock is unavailable, a lightweight regex
     parser runs locally (handles common MSSQL/PG patterns).

Produces the EXACT same JSON structure as the live DB extraction flow
(connectors.py -> extract_metadata).
"""

from __future__ import annotations

import json
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def decode_sql_bytes(raw: bytes) -> str:
    """
    Robustly decode raw SQL file bytes to a string.

    Handles (in order):
      1. UTF-16 LE with BOM  (\\xff\\xfe) — SSMS default export
      2. UTF-16 BE with BOM  (\\xfe\\xff)
      3. UTF-8 with BOM      (\\xef\\xbb\\xbf)
      4. UTF-8 without BOM
      5. Windows-1252 / latin-1 fallback

    This is critical because SSMS saves scripts as UTF-16 LE, and decoding
    UTF-16 as latin-1 produces null bytes that break all regex matching.
    """
    if not raw:
        return ""

    # UTF-16 LE BOM: FF FE
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")

    # UTF-16 BE BOM: FE FF
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff")

    # UTF-8 BOM: EF BB BF
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")

    # Heuristic: if the second byte is null, it's likely UTF-16 LE without BOM
    # (common for some SSMS versions)
    if len(raw) > 2 and raw[1] == 0:
        try:
            return raw.decode("utf-16-le", errors="replace")
        except Exception:
            pass

    # Try UTF-8 (most common for modern tools)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # Final fallback: Windows-1252 / latin-1 (never raises)
    return raw.decode("latin-1")



# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Python Pre-filter (strip everything except DDL)
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_comments(sql: str) -> str:
    """Remove SQL comments (line and block) without disturbing string literals."""
    result = []
    i = 0
    n = len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                elif sql[j] == "'":
                    j += 1
                    break
                else:
                    j += 1
            result.append(sql[i:j])
            i = j
        elif sql[i:i + 2] == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            result.append(" ")
        elif sql[i:i + 2] == "--":
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end + 1
            result.append("\n")
        else:
            result.append(sql[i])
            i += 1
    return "".join(result)


def _extract_ddl_only(sql: str) -> str:
    """
    Pre-filter: keep only CREATE TABLE and ALTER TABLE ... ADD CONSTRAINT
    statements. Strips all DML, SET, USE, INSERT, GO, etc.

    Returns condensed DDL text (typically 90%+ smaller than the original).
    """
    # Step 1: Strip comments
    clean = _strip_comments(sql)

    # Step 2: Normalise line endings (CRLF -> LF) BEFORE the GO replacement
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")

    # Step 3: Replace GO batch separator (on its own line) with ;
    # (?m) + $ matches end-of-line (before \n) after normalisation
    clean = re.sub(r"(?im)^[ \t]*GO[ \t]*$", ";", clean)

    # Step 4: Split into statements on ; 
    raw_statements = clean.split(";")

    # Step 5: Keep only DDL statements
    ddl_statements = []
    for stmt in raw_statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        # Normalise whitespace for keyword matching
        collapsed = re.sub(r"\s+", " ", stmt)
        upper = collapsed.upper()

        # Keep CREATE TABLE
        if "CREATE TABLE" in upper:
            ddl_statements.append(stmt)
        # Keep ANY ALTER TABLE that adds a PK or FK
        elif "ALTER TABLE" in upper and ("PRIMARY KEY" in upper or "FOREIGN KEY" in upper):
            ddl_statements.append(stmt)
        # Skip everything else (INSERT, SET, USE, GRANT, etc.)

    return ";\n\n".join(ddl_statements) + ";" if ddl_statements else ""


def _count_ddl_tokens_approx(ddl: str) -> int:
    """Rough token estimate (1 token ≈ 4 chars for English/SQL)."""
    return len(ddl) // 4


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — LLM Parsing (AWS Bedrock)
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """You are a database schema extraction tool.  You receive SQL DDL
statements and return a structured JSON object describing every table, its columns,
primary keys, foreign keys, and data types.

RULES:
- Return ONLY valid JSON, no markdown fences, no explanation.
- Include ALL tables found in the DDL.
- For each column, determine: column_name, data_type (base type like INT, VARCHAR,
  DECIMAL, DATETIME, BIT — no size), nullable (true/false), unique (true/false), is_generated (true if
  IDENTITY or auto-increment), primary_key (true/false), foreign_key (true/false),
  references (object with schema/table/column if FK, else null).
- For each table include a "description" field set to null.
- distinct_count for every column should be null.
- description for every column should be null.
- If a schema is specified (e.g. [dbo].[Table]), use it; otherwise use the
  default_schema provided.
"""

_USER_PROMPT_TEMPLATE = """Extract ALL tables and their column metadata from this SQL DDL.
Return the result as a JSON object with this EXACT structure:

{{
  "tables": [
    {{
      "schema_name": "dbo",
      "table_name": "TableName",
      "table_type": "BASE TABLE",
      "row_count": null,
      "size": null,
      "description": null,
      "columns": [
        {{
          "column_name": "ColName",
          "data_type": "INT",
          "nullable": false,
          "unique": false,
          "is_generated": true,
          "primary_key": true,
          "foreign_key": false,
          "references": null,
          "distinct_count": null,
          "description": null
        }}
      ]
    }}
  ]
}}

Default schema: {default_schema}
Database type: {db_type}

SQL DDL:
{ddl}
"""


def _parse_with_llm(
    ddl: str,
    db_type: str = "mssql",
    default_schema: str = "dbo",
) -> Optional[List[Dict[str, Any]]]:
    """
    Call AWS Bedrock to parse DDL into structured table metadata.
    Returns a list of table dicts or None on failure.
    """
    import concurrent.futures

    try:
        from ai_utils import _invoke
    except ImportError:
        logger.warning("ai_utils not available, cannot use LLM parsing.")
        return None

    # Step 1: Chunk the DDL into manageable batches
    # We split by ; which separates the DDL statements
    statements = [s.strip() for s in ddl.split(";") if s.strip()]
    
    # If the file is small, just do one batch
    chunk_size = 15
    chunks = []
    for i in range(0, len(statements), chunk_size):
        chunk_ddl = ";\n\n".join(statements[i:i + chunk_size]) + ";"
        chunks.append((i // chunk_size, chunk_ddl))

    logger.info(f"Split {len(statements)} DDL statements into {len(chunks)} chunks for parallel Bedrock processing.")

    import time
    
    def _process_chunk(chunk_index: int, chunk_text: str) -> Optional[List[Dict[str, Any]]]:
        # Stagger the start of each chunk to avoid hitting the API rate limit perfectly at the same millisecond
        # Stagger by 2 seconds per chunk index
        if chunk_index > 0:
            time.sleep(chunk_index * 2.0)
            
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            ddl=chunk_text,
            db_type=db_type,
            default_schema=default_schema,
        )
        
        approx_tok = _count_ddl_tokens_approx(chunk_text)
        max_out = max(2000, approx_tok * 4) # Generous budget for JSON
        
        logger.debug(f"[Chunk {chunk_index}] Sending ~{approx_tok} tokens to Bedrock (max_out={max_out})")
        
        raw_response = ""
        max_retries = 3
        
        for attempt in range(max_retries):
            raw_response = _invoke(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=max_out,
            )
            
            if raw_response:
                break
                
            if attempt < max_retries - 1:
                backoff = (attempt + 1) * 5
                logger.warning(f"[Chunk {chunk_index}] Bedrock invoke failed or empty. Retrying in {backoff}s...")
                time.sleep(backoff)

        if not raw_response:
            logger.error(f"[Chunk {chunk_index}] Bedrock returned empty response after {max_retries} attempts.")
            return None

        json_text = raw_response.strip()
        if json_text.startswith("```"):
            json_text = re.sub(r"^```(?:json)?\s*", "", json_text)
            json_text = re.sub(r"\s*```\s*$", "", json_text)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"[Chunk {chunk_index}] Failed to parse LLM JSON: {e}")
            return None

        tables = parsed.get("tables", parsed if isinstance(parsed, list) else [])
        if not isinstance(tables, list):
            logger.error(f"[Chunk {chunk_index}] Unexpected LLM response shape: {type(tables)}")
            return None
            
        return tables

    # Step 2: Execute chunks in parallel
    all_tables: List[Dict[str, Any]] = []
    has_error = False

    max_workers = min(10, len(chunks)) if chunks else 1
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_chunk = {
            executor.submit(_process_chunk, idx, text): idx 
            for idx, text in chunks
        }
        
        for future in concurrent.futures.as_completed(future_to_chunk):
            chunk_idx = future_to_chunk[future]
            try:
                result = future.result()
                if result is None:
                    has_error = True
                    logger.error(f"Chunk {chunk_idx} failed.")
                else:
                    all_tables.extend(result)
            except Exception as exc:
                has_error = True
                logger.error(f"Chunk {chunk_idx} generated an exception: {exc}")

    # If any chunk failed, fail the whole LLM parse so we safely fall back to regex
    if has_error:
        logger.warning("One or more LLM batches failed. Abandoning LLM parse for this file.")
        return None

    return all_tables


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Fallback Regex Parser (runs if Bedrock unavailable)
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _clean_name(name: str) -> str:
    s = name.strip()
    s = re.sub(r"\s+(?:ASC|DESC)\s*$", "", s, flags=re.IGNORECASE)
    s = s.strip()
    m = re.match(r"^\[([^\]]+)\]", s)
    if m:
        return m.group(1)
    m = re.match(r'^"([^"]+)"', s)
    if m:
        return m.group(1)
    m = re.match(r"^`([^`]+)`", s)
    if m:
        return m.group(1)
    return s


def _strip_brackets(text: str) -> str:
    return text.replace("[", "").replace("]", "")


def _split_top_level(body: str) -> List[str]:
    parts = []
    depth = 0
    current: List[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        last = "".join(current).strip()
        if last:
            parts.append(last)
    return parts


_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\[?(\w+)\]?\.)?"
    r"\[?(\w+)\]?"
    r"\s*\(",
    re.IGNORECASE,
)

_RE_DATA_TYPE = re.compile(
    r"^((?:TINY|SMALL|BIG)?INT(?:EGER)?|SERIAL|"
    r"(?:N?(?:VAR)?CHAR|CHARACTER\s+VARYING|TEXT|NTEXT|CLOB|NCLOB)|"
    r"DECIMAL|NUMERIC|FLOAT|DOUBLE(?:\s+PRECISION)?|REAL|"
    r"MONEY|SMALLMONEY|"
    r"DATE(?:TIME)?(?:2)?(?:OFFSET)?|SMALLDATETIME|TIME(?:STAMP)?|"
    r"TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|"
    r"BIT|BOOLEAN|BOOL|"
    r"BINARY|VARBINARY|IMAGE|BLOB|"
    r"UNIQUEIDENTIFIER|UUID|XML|JSON|JSONB|"
    r"NUMBER|VARCHAR2|NVARCHAR2|RAW|LONG|VARIANT|OBJECT|ARRAY"
    r")(?:\s*\([^)]*\))?",
    re.IGNORECASE,
)

_RE_ALTER_TABLE = re.compile(
    r"ALTER\s+TABLE\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?",
    re.IGNORECASE,
)

_RE_ADD_PK = re.compile(
    r"(?:WITH\s+(?:NO)?CHECK\s+)?ADD\s+(?:CONSTRAINT\s+\[?\w+\]?\s+)?PRIMARY\s+KEY\s*"
    r"(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]+)\)",
    re.IGNORECASE,
)

_RE_ADD_FK = re.compile(
    r"(?:WITH\s+(?:NO)?CHECK\s+)?ADD\s+(?:CONSTRAINT\s+\[?\w+\]?\s+)?FOREIGN\s+KEY\s*"
    r"\(([^)]+)\)\s*REFERENCES\s+"
    r"(?:\[?(\w+)\]?\.)?\[?(\w+)\]?\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def _parse_column_def_regex(col_text: str) -> Optional[Dict[str, Any]]:
    text = col_text.strip()
    text = re.sub(r"\s+ON\s+\[\w+\]\s*$", "", text, flags=re.IGNORECASE)
    upper = text.upper().strip()

    if upper.startswith(("CONSTRAINT", "PRIMARY KEY", "FOREIGN KEY",
                         "UNIQUE", "CHECK", "INDEX", "KEY ")):
        return None

    if text.startswith("["):
        end_bracket = text.find("]")
        if end_bracket == -1:
            return None
        col_name = text[1:end_bracket]
        rest = text[end_bracket + 1:].strip()
    elif text.startswith('"'):
        end_quote = text.find('"', 1)
        if end_quote == -1:
            return None
        col_name = text[1:end_quote]
        rest = text[end_quote + 1:].strip()
    else:
        tokens = text.split(None, 1)
        if len(tokens) < 2:
            return None
        col_name = tokens[0]
        rest = tokens[1]

    col_name = _clean_name(col_name)
    rest_clean = _strip_brackets(rest)

    dt_match = _RE_DATA_TYPE.match(rest_clean)
    if dt_match:
        data_type = dt_match.group(0).upper()
        base = re.match(r"^(\w[\w\s]*\w|\w+)", data_type)
        data_type = base.group(0).strip() if base else data_type
        data_type = re.sub(r"\s+", " ", data_type)
    else:
        first_word = rest_clean.split("(")[0].split()[0] if rest_clean.strip() else "UNKNOWN"
        data_type = _clean_name(first_word).upper()

    nullable = not bool(re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE))
    is_unique = bool(re.search(r"\bUNIQUE\b", rest, re.IGNORECASE))
    is_generated = bool(re.search(r"\bIDENTITY\b", rest, re.IGNORECASE))
    primary_key = bool(re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE))

    foreign_key = False
    references = None
    fk_m = re.search(r"\bREFERENCES\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?\s*\(([^)]+)\)", rest, re.IGNORECASE)
    if fk_m:
        foreign_key = True
        references = {
            "schema": fk_m.group(1) or "dbo",
            "table": _clean_name(fk_m.group(2)),
            "column": _clean_name(fk_m.group(3)),
        }

    return {
        "column_name": col_name,
        "data_type": data_type,
        "nullable": nullable,
        "unique": is_unique,
        "is_generated": is_generated,
        "primary_key": primary_key,
        "foreign_key": foreign_key,
        "references": references,
        "distinct_count": None,
        "description": None,
    }


def _extract_body(stmt: str, start: int) -> str:
    depth = 0
    i = start
    body_start = None
    n = len(stmt)
    while i < n:
        if stmt[i] == "(":
            if depth == 0:
                body_start = i + 1
            depth += 1
        elif stmt[i] == ")":
            depth -= 1
            if depth == 0:
                return stmt[body_start:i]
        i += 1
    return stmt[body_start:] if body_start else ""


def _regex_parse_tables(
    ddl_statements: List[str],
    default_schema: str,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Parse DDL statements using regex. Returns schema -> table_name -> table_dict."""
    tables_by_schema: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for stmt in ddl_statements:
        normalized = _normalize(stmt)
        upper = normalized.upper()

        if "CREATE TABLE" in upper:
            m = _RE_CREATE_TABLE.search(normalized)
            if not m:
                continue
            schema = m.group(1) or default_schema
            table_name = m.group(2)

            paren_pos = normalized.find("(", m.end() - 1)
            if paren_pos == -1:
                continue

            body = _extract_body(normalized, paren_pos)
            parts = _split_top_level(body)

            columns = []
            constraint_parts = []
            for part in parts:
                col = _parse_column_def_regex(part)
                if col:
                    columns.append(col)
                else:
                    constraint_parts.append(part)

            # Apply table-level constraints (PK/FK inside CREATE TABLE body)
            col_idx = {c["column_name"].lower(): c for c in columns}
            for cp in constraint_parts:
                pk_m = re.search(r"PRIMARY\s+KEY\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]+)\)", cp, re.IGNORECASE)
                if pk_m:
                    for cn in pk_m.group(1).split(","):
                        n = _clean_name(cn).lower()
                        if n in col_idx:
                            col_idx[n]["primary_key"] = True
                uq_m = re.search(r"UNIQUE\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]+)\)", cp, re.IGNORECASE)
                if uq_m:
                    for cn in uq_m.group(1).split(","):
                        n = _clean_name(cn).lower()
                        if n in col_idx:
                            col_idx[n]["unique"] = True
                fk_m = re.search(
                    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?\s*\(([^)]+)\)",
                    cp, re.IGNORECASE,
                )
                if fk_m:
                    fk_cols = [_clean_name(c) for c in fk_m.group(1).split(",")]
                    ref_schema = fk_m.group(2) or default_schema
                    ref_table = _clean_name(fk_m.group(3))
                    ref_cols = [_clean_name(c) for c in fk_m.group(4).split(",")]
                    for fc, rc in zip(fk_cols, ref_cols):
                        if fc.lower() in col_idx:
                            col_idx[fc.lower()]["foreign_key"] = True
                            col_idx[fc.lower()]["references"] = {
                                "schema": ref_schema, "table": ref_table, "column": rc,
                            }

            if schema not in tables_by_schema:
                tables_by_schema[schema] = {}
            tables_by_schema[schema][table_name] = {
                "table_name": table_name,
                "table_type": "BASE TABLE",
                "row_count": None,
                "size": None,
                "columns": columns,
                "description": None,
            }

        elif "ALTER TABLE" in upper and ("ADD CONSTRAINT" in upper or "PRIMARY KEY" in upper or "FOREIGN KEY" in upper):
            m = _RE_ALTER_TABLE.match(normalized)
            if not m:
                continue
            schema = m.group(1) or default_schema
            table_name = m.group(2)
            rest = normalized[m.end():]

            table_dict = None
            if schema in tables_by_schema and table_name in tables_by_schema[schema]:
                table_dict = tables_by_schema[schema][table_name]
            else:
                for s_tables in tables_by_schema.values():
                    if table_name in s_tables:
                        table_dict = s_tables[table_name]
                        break
            if table_dict is None:
                continue

            col_idx = {c["column_name"].lower(): c for c in table_dict["columns"]}

            pk_m = _RE_ADD_PK.search(rest)
            if pk_m:
                for cn in pk_m.group(1).split(","):
                    n = _clean_name(cn).lower()
                    if n in col_idx:
                        col_idx[n]["primary_key"] = True

            uq_m = re.search(r"(?:WITH\s+(?:NO)?CHECK\s+)?ADD\s+(?:CONSTRAINT\s+\[?\w+\]?\s+)?UNIQUE\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]+)\)", rest, re.IGNORECASE)
            if uq_m:
                for cn in uq_m.group(1).split(","):
                    n = _clean_name(cn).lower()
                    if n in col_idx:
                        col_idx[n]["unique"] = True

            fk_m = _RE_ADD_FK.search(rest)
            if fk_m:
                fk_cols = [_clean_name(c) for c in fk_m.group(1).split(",")]
                ref_schema = fk_m.group(2) or default_schema
                ref_table = _clean_name(fk_m.group(3))
                ref_cols = [_clean_name(c) for c in fk_m.group(4).split(",")]
                for fc, rc in zip(fk_cols, ref_cols):
                    if fc.lower() in col_idx:
                        col_idx[fc.lower()]["foreign_key"] = True
                        col_idx[fc.lower()]["references"] = {
                            "schema": ref_schema, "table": ref_table, "column": rc,
                        }

    return tables_by_schema


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def parse_sql_to_metadata(
    sql_content: str,
    db_type: str = "mssql",
    database_name: str = "",
    default_schema: str = "dbo",
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Parse SQL DDL content and produce the metadata JSON structure.

    Flow:
      1. Python pre-filter — extract only DDL (CREATE TABLE, ALTER TABLE)
      2. If use_llm=True, send condensed DDL to AWS Bedrock (Claude)
      3. If Bedrock fails or use_llm=False, fall back to regex parser
      4. Build the final metadata dict

    Parameters
    ----------
    sql_content : str
        Raw SQL text (may contain DDL, DML, comments, etc.).
    db_type : str
        Database type label (mssql, postgres, oracle, snowflake).
    database_name : str
        Database name (empty if unknown).
    default_schema : str
        Default schema name (default: "dbo").
    use_llm : bool
        Whether to use AWS Bedrock for parsing (default: True).

    Returns
    -------
    dict
        Metadata dict matching db_extraction_schema output shape.
    """
    # Normalise line endings (Windows CRLF -> LF) upfront
    sql_content = sql_content.replace("\r\n", "\n").replace("\r", "\n")

    # Try to extract database name from USE [DatabaseName]
    use_match = re.search(r"\bUSE\s+\[?(\w+)\]?", sql_content, re.IGNORECASE)
    if use_match and not database_name:
        database_name = use_match.group(1)

    # ── Step 1: Pre-filter DDL (still useful for LLM fallback logging) ─────
    ddl_text = _extract_ddl_only(sql_content)
    
    # ── Step 2: Try Regex Parser First (Lightning Fast) ────────────────────
    logger.info("Running fast regex parser...")
    clean = _strip_comments(sql_content)
    clean = re.sub(r"(?im)^[ \t]*GO[ \t]*$", ";", clean)
    stmts = [s.strip() for s in clean.split(";") if s.strip()]

    tables_by_schema = _regex_parse_tables(stmts, default_schema)
    regex_table_count = sum(len(tbls) for tbls in tables_by_schema.values())
    
    if regex_table_count > 0:
        logger.info(f"Regex parser succeeded, found {regex_table_count} tables. Skipping LLM.")
        return _build_output_from_schema_dict(
            tables_by_schema, db_type, database_name, default_schema,
        )

    # ── Step 3: LLM Fallback (if regex found nothing and use_llm=True) ─────
    if not use_llm:
        logger.warning("Regex found 0 tables and use_llm=False. Returning empty schema.")
        return _build_output_from_schema_dict({}, db_type, database_name, default_schema)

    if not ddl_text.strip():
        logger.warning("No DDL statements found after pre-filtering.")
        return _build_output([], db_type, database_name, default_schema)

    original_size = len(sql_content)
    ddl_size = len(ddl_text)
    reduction = ((original_size - ddl_size) / original_size * 100) if original_size else 0
    logger.info(
        f"Regex fallback to LLM. Pre-filter: {original_size:,} chars -> {ddl_size:,} chars "
        f"({reduction:.0f}% reduction)"
    )

    tables = None
    try:
        tables = _parse_with_llm(ddl_text, db_type, default_schema)
        if tables:
            logger.info(f"LLM parsed {len(tables)} tables successfully.")
    except Exception as e:
        logger.error(f"LLM parsing failed: {e}")

    # ── Step 4: Build output from LLM result (if successful) ───────────────
    if tables is None:
        tables = []
        
    return _build_output(tables, db_type, database_name, default_schema)


def _build_output(
    tables: List[Dict[str, Any]],
    db_type: str,
    database_name: str,
    default_schema: str,
) -> Dict[str, Any]:
    """Build the final metadata output from a list of table dicts (LLM format)."""
    # Group tables by schema
    schemas_dict: Dict[str, List[Dict[str, Any]]] = {}
    for table in tables:
        schema_name = table.pop("schema_name", default_schema)
        if schema_name not in schemas_dict:
            schemas_dict[schema_name] = []
        # Ensure all expected fields exist
        for col in table.get("columns", []):
            col.setdefault("distinct_count", None)
            col.setdefault("description", None)
            col.setdefault("references", None)
            col.setdefault("is_generated", False)
            col.setdefault("unique", False)
        table.setdefault("table_type", "BASE TABLE")
        table.setdefault("row_count", None)
        table.setdefault("size", None)
        table.setdefault("description", None)
        schemas_dict[schema_name].append(table)

    schemas = [
        {"schema_name": sn, "tables": tbls}
        for sn, tbls in schemas_dict.items()
    ]

    all_schemas = list(schemas_dict.keys())
    primary_schema = all_schemas[0] if all_schemas else default_schema

    return {
        "source": {
            "db_type": db_type,
            "database": database_name,
            "schema": primary_schema,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        },
        "column_definition": [
            "column_name", "data_type", "nullable", "unique", "is_generated",
            "primary_key", "foreign_key", "references",
        ],
        "schemas": schemas,
    }


def _build_output_from_schema_dict(
    tables_by_schema: Dict[str, Dict[str, Dict[str, Any]]],
    db_type: str,
    database_name: str,
    default_schema: str,
) -> Dict[str, Any]:
    """Build the final metadata output from the regex parser's schema dict."""
    schemas = [
        {"schema_name": sn, "tables": list(tbls.values())}
        for sn, tbls in tables_by_schema.items()
    ]

    all_schemas = list(tables_by_schema.keys())
    primary_schema = all_schemas[0] if all_schemas else default_schema

    return {
        "source": {
            "db_type": db_type,
            "database": database_name,
            "schema": primary_schema,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        },
        "column_definition": [
            "column_name", "data_type", "nullable", "is_generated",
            "primary_key", "foreign_key", "references",
        ],
        "schemas": schemas,
    }


def parse_sql_files_to_metadata(
    file_contents: List[str],
    db_type: str = "mssql",
    database_name: str = "",
    default_schema: str = "dbo",
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Parse multiple SQL file contents and merge into a single metadata output.
    """
    combined = "\n".join(file_contents)
    return parse_sql_to_metadata(
        sql_content=combined,
        db_type=db_type,
        database_name=database_name,
        default_schema=default_schema,
        use_llm=use_llm,
    )
