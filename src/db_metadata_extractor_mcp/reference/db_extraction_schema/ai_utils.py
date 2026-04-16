import os
import json
import re
from typing import List, Any, Dict, Optional
import boto3
from dotenv import load_dotenv
from pathlib import Path

# Ensure we load the .env file that sits next to this module (fast_api/.env)
base_dir = Path(__file__).resolve().parent
# Load the .env that sits next to this module
load_dotenv(dotenv_path=base_dir / ".env", override=False)

# ── AWS Bedrock Configuration ─────────────────────────────────────────────────
AWS_ACCESS_KEY    = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY    = os.getenv("AWS_SECRET_KEY")
AWS_REGION        = os.getenv("AWS_REGION") or os.getenv("AWS_REGION_NAME", "us-east-1")
BEDROCK_MODEL_ID  = os.getenv("MODEL_ID") or os.getenv("BEDROCK_CLAUDE_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")

_bedrock_client = None

def _get_client():
    global _bedrock_client
    if _bedrock_client is not None:
        return _bedrock_client
    try:
        session = boto3.Session(
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name=AWS_REGION,
        )
        _bedrock_client = session.client("bedrock-runtime")
        return _bedrock_client
    except Exception as e:
        print(f"Warning: Failed to initialize Bedrock client: {e}")
        return None


def _invoke(system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """
    Call AWS Bedrock using the Converse API.
    Returns the assistant text response, or "" on failure.
    """
    client = _get_client()
    if client is None:
        return ""
    try:
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
        return response["output"]["message"]["content"][0]["text"].strip()
    except Exception as e:
        print(f"Bedrock invoke error: {e}")
        return ""


def generate_table_description_logic(
    table_name: str,
    columns: List[Any],
    primary_keys: List[str],
    foreign_keys: List[Any],
) -> str:
    """Generate a business-friendly table description using AWS Bedrock."""
    system_prompt = (
        "You are a senior business data analyst. Your job is to help business users "
        "understand database tables in plain, non-technical language. "
        "Given metadata about a table, write a clear, executive-level summary that "
        "explains what business purpose this table serves, what kind of information it "
        "contains, and how it might be used by non-technical stakeholders. "
        "Focus on business context, use cases, and relevance. Avoid technical jargon."
    )
    user_prompt = (
        f"Table Name: {table_name}\n"
        f"Columns: {json.dumps(columns[:50])}\n"
        f"Primary Keys: {json.dumps(primary_keys)}\n"
        f"Foreign Keys: {json.dumps(foreign_keys)}\n\n"
        "Write a 2-4 sentence executive summary for business users. Explain what this "
        "table represents, why it matters, and what business questions it helps answer. "
        "Avoid technical details and markdown formatting."
    )

    content = _invoke(system_prompt, user_prompt, max_tokens=400)
    if content:
        content = re.sub(r"[*`]+", "", content)
        return content

    # Heuristic fallback when AI is unavailable
    col_names = ", ".join(
        [c.get("name") or c.get("column_name") or str(c) for c in columns[:10]]
    )
    pk_part = f" Primary key(s): {', '.join(primary_keys)}." if primary_keys else ""
    return f"Table {table_name} with columns: {col_names}.{pk_part}"


def generate_column_descriptions_logic(
    table_name: str,
    columns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate business-friendly column descriptions using AWS Bedrock."""
    system_prompt = (
        "You are a senior business data analyst. Your job is to help business users "
        "understand database columns in plain, non-technical language. "
        "Given metadata about columns, write clear, business-friendly descriptions for "
        "each column, focusing on what information it contains and how it is relevant "
        "to business users. Avoid technical jargon and keep descriptions concise."
    )
    user_prompt = (
        f"Table: {table_name}\n"
        f"Columns: {json.dumps(columns)}\n\n"
        'For each column, generate a short, business-friendly description (1-2 sentences) '
        'explaining what the column represents and why it is important for business users. '
        'Return a JSON object with a top-level key "columns" containing an array of objects '
        'like: {"columnName": "...", "description": "..."}. Only return JSON. Avoid markdown.'
    )

    content = _invoke(system_prompt, user_prompt, max_tokens=800)
    if content:
        try:
            ai_data = json.loads(content)
        except Exception:
            m = re.search(r"(\{.*\}|\[.*\])", content, re.S)
            if m:
                try:
                    ai_data = json.loads(m.group(1))
                except Exception:
                    ai_data = None
            else:
                ai_data = None

        if isinstance(ai_data, dict) and "columns" in ai_data:
            return ai_data["columns"]
        if isinstance(ai_data, list):
            return ai_data

    # Heuristic fallback
    results = []
    for c in columns:
        name = c.get("name") or c.get("columnName") or c.get("column_name") or str(c)
        results.append({"columnName": name, "description": f"Column {name} in table {table_name}."})
    return results


def generate_table_and_column_descriptions_logic(
    table_name: str, 
    columns: List[Dict[str, Any]], 
    primary_keys: List[str], 
    foreign_keys: List[Any]
) -> Dict[str, Any]:
    """
    Generate BOTH table description AND column descriptions in one AI call.
    Returns: {"table_description": "...", "column_descriptions": [{"columnName": "...", "description": "..."}, ...]}
    """
    system_prompt = (
        "You are a senior business data analyst. Your job is to help business users understand database structures in plain, non-technical language. "
        "Given a table and its columns, provide a business-level summary of the table and concise descriptions for each column. "
        "Focus on business context and relevance. Avoid technical jargon."
    )
    
    user_prompt = f"""
    Table Name: {table_name}
    Primary Keys: {json.dumps(primary_keys)}
    Foreign Keys: {json.dumps(foreign_keys)}
    Columns: {json.dumps(columns)}

    Tasks:
    1. Write a 2-4 sentence executive summary of the table's business purpose.
    2. Write a 1-sentence business-friendly description for EVERY column.

    Return the results as a JSON object with this exact structure:
    {{
      "table_description": "...",
      "column_descriptions": [
         {{"columnName": "...", "description": "..."}},
         ...
      ]
    }}
    Only return JSON. Avoid markdown.
    """

    content = _invoke(system_prompt, user_prompt, max_tokens=1500)
    if content:
        try:
            ai_data = json.loads(content)
        except Exception:
            m = re.search(r"(\{.*\}|\[.*\])", content, re.S)
            if m:
                try:
                    ai_data = json.loads(m.group(1))
                except Exception:
                    ai_data = None
            else:
                ai_data = None

        if isinstance(ai_data, dict) and "table_description" in ai_data:
            return ai_data

    # Fallback logic
    table_desc = f"Table {table_name} containing business data."
    col_descs = []
    for c in columns:
        name = c.get("name") or c.get("column_name") or str(c)
        col_descs.append({"columnName": name, "description": f"Details for {name}."})
        
    return {
        "table_description": table_desc,
        "column_descriptions": col_descs
    }


def generate_adaptive_batch_descriptions_logic(tables_batch: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate descriptions for a BATCH of tables in one AI call using AWS Bedrock.
    Each table in the batch has keys: 'table_name', 'columns', 'primary_keys', 'foreign_keys'.
    Returns a dict keyed by table_name: { "table_name": { "table_description": "...", "column_descriptions": [...] } }
    """
    system_prompt = (
        "You are a senior business data analyst. Your job is to help business users understand database structures in plain, non-technical language. "
        "Summarize the purpose of each table provided and generate concise, business-friendly descriptions for every column. "
        "Focus on business context and relevance. Avoid technical jargon."
    )
    
    # We pass the batch as a list of simplified table objects to save tokens
    batch_info = []
    for t in tables_batch:
        batch_info.append({
            "table_name": t["table_name"],
            "primary_keys": t["primary_keys"],
            "foreign_keys": t["foreign_keys"],
            "columns": t["columns"][:50] # Limit to 50 columns to stay safe
        })

    user_prompt = f"""
    Batch of Tables: {json.dumps(batch_info)}

    For EVERY table in the batch, generate:
    1. A 2-4 sentence executive summary of the table's purpose.
    2. A 1-sentence business-friendly description for EVERY column.

    Return the results as a SINGLE JSON object where EACH KEY is the table name:
    {{
      "Table_Name_A": {{
        "table_description": "...",
        "column_descriptions": [
           {{"columnName": "...", "description": "..."}},
           ...
        ]
      }},
      "Table_Name_B": {{ ... }}
    }}
    Only return JSON. Avoid markdown.
    """

    content = _invoke(system_prompt, user_prompt, max_tokens=3500)
    if content:
        try:
            ai_data = json.loads(content)
        except Exception:
            m = re.search(r"(\{.*\}|\[.*\])", content, re.S)
            if m:
                try:
                    ai_data = json.loads(m.group(1))
                except Exception:
                    ai_data = None
            else:
                ai_data = None

        if isinstance(ai_data, dict):
            # Verify that we got data for at least one table
            return ai_data

    # Final fallback: map inputs back with empty/placeholder values
    return {
        t["table_name"]: {
            "table_description": f"Table {t['table_name']} containing business data.",
            "column_descriptions": [{"columnName": c.get("name") or c.get("column_name"), "description": ""} for c in t["columns"]]
        } for t in tables_batch
    }


def infer_primary_keys_logic(table_name: str, columns: List[Dict[str, Any]]) -> List[str]:
    """
    Infer primary keys for a single table using AI heuristics.
    Looks for standard naming conventions (id, _id, _key) and leverages unique/nullable constraints.
    Returns a list of column names that form the primary key.
    """
    system_prompt = (
        "You are an expert database architect. Your task is to infer the SINGLE best Primary Key (PK) "
        "column for a given table based on column metadata. "
        "Primary keys uniquely identify a row. Strong signals include: "
        "\n1. Names like 'id', 'user_id', 'customer_key', 'pk_id'."
        "\n2. Constraints: 'nullable: false' and 'unique: true' are extremely strong indicators."
        "\n3. Data types like integer, UUID, or identity columns."
        "\nSTRICT RULE: Only return ONE column name. If multiple columns could form a key, pick the most appropriate one (e.g., 'id')."
        "\nReturn ONLY a JSON array of ONE string containing the exact column name. Example: [\"id\"]"
        "\nIf no clear primary key can be inferred, return an empty array []."
    )
    
    # Strip unnecessary noise from columns to save tokens
    simplified_cols = [
        {
            "name": c.get("column_name") or c.get("name"), 
            "type": c.get("data_type"), 
            "nullable": c.get("nullable", True), 
            "unique": c.get("unique", False)
        }
        for c in columns
    ]
    
    user_prompt = f"""
    Table Name: {table_name}
    Columns: {json.dumps(simplified_cols)}
    
    Return ONLY a JSON array of column names: ["col1", "col2"]. Do not wrap in markdown or explain.
    """
    
    content = _invoke(system_prompt, user_prompt, max_tokens=500)
    if content:
        try:
            # Robust extraction of the first JSON array found
            m = re.search(r"(\[.*\])", content, re.S)
            if m:
                extracted = m.group(1)
                # Further cleanup if markdown-like backticks are present
                extracted = re.sub(r"^```json\s*", "", extracted)
                extracted = re.sub(r"```$", "", extracted)
                inferred = json.loads(extracted)
                if isinstance(inferred, list) and all(isinstance(x, str) for x in inferred):
                    return inferred
        except Exception:
            pass
            
    return []


def infer_foreign_keys_logic(schema_context: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Infer foreign key relationships based on schema context.
    Schema context contains simplified representations of all tables in the database chunk, 
    including their inferred/existing primary keys.
    Returns a list of foreign key relationships.
    """
    system_prompt = (
        "You are an expert database architect. Your task is to infer missing Foreign Key (FK) "
        "relationships between tables given a schema context. "
        "Look for columns in a source table (e.g., 'customer_id') that refer to the primary key "
        "of a target table (e.g., table 'customers', column 'id' or 'customer_id'). "
        "Return the relationships as a JSON array of objects with EXACTLY this structure: "
        '[{"source_table": "...", "source_column": "...", "target_schema": "...", "target_table": "...", "target_column": "..."}]'
        "\nReturn ONLY valid JSON. If no foreign keys can be inferred, return an empty array []."
    )
    
    user_prompt = f"""
    Schema Context (List of Tables):
    {json.dumps(schema_context)}
    
    Return ONLY a JSON array of inferred foreign key relationship objects. Do not wrap in markdown or explain logic.
    """
    
    content = _invoke(system_prompt, user_prompt, max_tokens=4000)
    if content:
        try:
            # Robust extraction of the first JSON array found
            m = re.search(r"(\[.*\])", content, re.S)
            if m:
                extracted = m.group(1)
                # Further cleanup if markdown-like backticks are present
                extracted = re.sub(r"^```json\s*", "", extracted)
                extracted = re.sub(r"```$", "", extracted)
                inferred = json.loads(extracted)
                if isinstance(inferred, list) and all(isinstance(x, dict) for x in inferred):
                    return inferred
        except Exception as e:
            print(f"FK inference array parse error: {e}")
            pass
            
    return []
