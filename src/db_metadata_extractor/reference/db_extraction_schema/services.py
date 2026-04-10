from connectors import extract_metadata
from ai_utils import (
    generate_table_description_logic, 
    generate_column_descriptions_logic,
    generate_table_and_column_descriptions_logic,
    generate_adaptive_batch_descriptions_logic,
    infer_primary_keys_logic,
    infer_foreign_keys_logic
)
from typing import Dict, Any, List
from datetime import datetime, timezone
import concurrent.futures
import time


def extract_raw_metadata(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts raw metadata from the database WITHOUT AI descriptions.
    Returns the metadata dict (source + schemas).
    """
    db_type = request_data.get("db_type")
    connection_params = {
        "host": request_data.get("host"),
        "port": request_data.get("port"),
        "database_name": request_data.get("database_name"),
        "username": request_data.get("username"),
        "password": request_data.get("password"),
        "schema_name": request_data.get("schema_name"),
        "account": request_data.get("account"),
        "warehouse": request_data.get("warehouse"),
        "role_name": request_data.get("role_name"),
        "project_id": request_data.get("project_id"),
        "service_account_key": request_data.get("service_account_key"),
    }

    raw_metadata = extract_metadata(
        db_type=db_type,
        connection_params=connection_params,
        tables=request_data.get("tables"),
    )

    return raw_metadata


def enrich_metadata_with_ai(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes already-extracted metadata and adds AI-generated descriptions
    to tables and columns IN-PLACE, returning the enriched metadata.
    
    Optimized: Uses ADAPTIVE BATCHING.
    - Small tables (<= 15 columns) are grouped together (up to 4 per batch).
    - Large tables are processed solo to ensure detail and avoid token limits.
    - Batches are processed in parallel using ThreadPoolExecutor.
    """
    start_time = time.time()
    
    # 1. Collect all tables and determine their "complexity"
    table_objs = []
    for schema_item in metadata.get("schemas", []):
        for table in schema_item.get("tables", []):
            columns = table.get("columns", [])
            cols_for_ai = [
                {"name": c.get("column_name"), "type": c.get("data_type")}
                for c in columns
            ]
            pk_list = [c.get("column_name") for c in columns if c.get("primary_key")]
            fk_list = [c.get("column_name") for c in columns if c.get("foreign_key")]
            
            table_objs.append({
                "table_ref": table,
                "table_info": {
                    "table_name": table.get("table_name"),
                    "columns": cols_for_ai,
                    "primary_keys": pk_list,
                    "foreign_keys": fk_list
                },
                "col_count": len(columns)
            })

    if not table_objs:
        return metadata

    # 2. Adaptive Grouping Logic
    batches = []
    current_small_batch = []
    SMALL_THRESHOLD = 15  # Tables with > 15 columns are handled solo
    MAX_BATCH_SIZE = 4    # Up to 4 small tables per LLM call

    for obj in table_objs:
        if obj["col_count"] > SMALL_THRESHOLD:
            # Large table -> individual batch
            batches.append([obj])
        else:
            current_small_batch.append(obj)
            if len(current_small_batch) >= MAX_BATCH_SIZE:
                batches.append(current_small_batch)
                current_small_batch = []
    
    if current_small_batch:
        batches.append(current_small_batch)

    print(f"--- Starting Adaptive AI Enrichment. Organized {len(table_objs)} tables into {len(batches)} batches ---")

    def _process_batch(batch: List[Dict[str, Any]]):
        b_start = time.time()
        # Prepare data for AI call
        ai_payload = [item["table_info"] for item in batch]
        
        # Call the batch AI logic
        # Returns: { "table_name": { "table_description": "...", "column_descriptions": [...] } }
        results_map = generate_adaptive_batch_descriptions_logic(ai_payload)

        # Map results back to original metadata objects
        for item in batch:
            table_ref = item["table_ref"]
            name = table_ref.get("table_name")
            
            res = results_map.get(name)
            if res:
                table_ref["description"] = res.get("table_description", "")
                
                # Column mapping
                col_info_map = {
                    (c.get("columnName") or c.get("column_name")): c
                    for c in res.get("column_descriptions", [])
                }
                for col in table_ref.get("columns", []):
                    c_name = col.get("column_name")
                    ai_col = col_info_map.get(c_name, {})
                    col["description"] = ai_col.get("description", "")
            else:
                print(f"  [Warning] No AI description returned for table '{name}' in batch.")

        b_end = time.time()
        batch_names = [item["table_info"]["table_name"] for item in batch]
        print(f"  [AI Batch] Finished batch {batch_names} in {b_end - b_start:.2f}s")

    # 3. Execute Batches in Parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(_process_batch, batches)

    end_time = time.time()
    print(f"--- Adaptive AI Enrichment Complete. Total time: {end_time - start_time:.2f}s ---")

    return metadata


# ── Legacy function (kept for backward compatibility) ─────────────────────

def get_metadata_with_ai_service(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts metadata AND enriches it with AI descriptions in one call.
    This is the legacy combined function.
    """
    raw_metadata = extract_raw_metadata(request_data)
    enriched = enrich_metadata_with_ai(raw_metadata)
    return enriched


def infer_pk_fk_with_ai(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Infers missing Primary Keys and Foreign Keys using AI.
    Updates the metadata JSON IN-PLACE with `inferred_pk` and `inferred_fk`.
    """
    print("--- Starting AI PK/FK Inference ---")
    
    table_refs = {}
    schema_context = []
    
    # 1. Build schema context and run PK inference
    import json
    for schema_item in metadata.get("schemas", []):
        schema_name = schema_item.get("schema_name", "dbo")
        for table in schema_item.get("tables", []):
            table_name = table.get("table_name")
            columns = table.get("columns", [])
            
            # --- Clear previous inferred flags to ensure fresh state ---
            for c in columns:
                c.pop("inferred_pk", None)
                c.pop("inferred_fk", None)
                c.pop("inferred_fk_references", None)
            
            table_refs[(schema_name, table_name)] = table
            
            # 1.1 Run PK inference ONLY if no native PK exists
            has_existing_pk = any(c.get("primary_key") for c in columns)
            inferred_pks = []
            
            if not has_existing_pk:
                inferred_pks = infer_primary_keys_logic(table_name, columns)
                # STRICT RULE: Force only one primary key per table for clarity
                if len(inferred_pks) > 1:
                    inferred_pks = [inferred_pks[0]]
            
            # 1.2 Validate and Apply inferred PKs to table
            exiting_col_names = {c.get("column_name") for c in columns}
            for col_name in inferred_pks:
                if col_name in exiting_col_names:
                    for c in columns:
                        if c.get("column_name") == col_name:
                            # Apply as inferred if it's not natively marked
                            if not c.get("primary_key"):
                                c["inferred_pk"] = True
                else:
                    print(f"  [Warning] Inferred PK '{col_name}' does not exist in table '{table_name}'. Skipping.")
            
            # Identify the final PKs (native + inferred) for the FK context
            final_pks = []
            for c in columns:
                if c.get("primary_key") or c.get("inferred_pk"):
                    final_pks.append(c.get("column_name"))
                    
            # Add to schema context for FK inference
            schema_context.append({
                "schema": schema_name,
                "table": table_name,
                "primary_keys": final_pks,
                "columns": [
                    {
                        "name": c.get("column_name"),
                        "type": c.get("data_type")
                    } for c in columns
                ]
            })

    # 2. Token-Safe Chunking for FK Inference
    # We estimate ~4 chars per token. A safe limit for Claude 3.5 Sonnet is easily 50k+ tokens.
    # We will chunk the schema_context if it exceeds a 50,000 token limit.
    context_str = json.dumps(schema_context)
    estimated_tokens = len(context_str) // 4
    
    MAX_TOKENS = 50000
    chunks = []
    
    if estimated_tokens > MAX_TOKENS:
        print(f"  [Info] Schema context is large (~{estimated_tokens} tokens). Chunking...")
        current_chunk = []
        current_len = 0
        for table_ctx in schema_context:
            t_len = len(json.dumps(table_ctx)) // 4
            if current_len + t_len > MAX_TOKENS and current_chunk:
                chunks.append(current_chunk)
                current_chunk = [table_ctx]
                current_len = t_len
            else:
                current_chunk.append(table_ctx)
                current_len += t_len
        if current_chunk:
            chunks.append(current_chunk)
    else:
        chunks = [schema_context]

    # 3. Process FK Inference per chunk
    for chunk in chunks:
        inferred_fks = infer_foreign_keys_logic(chunk)
        
        # 4. Validate and Apply inferred FKs back to metadata
        for fk in inferred_fks:
            source_table_name = fk.get("source_table")
            source_col_name = fk.get("source_column")
            
            target_schema_name = fk.get("target_schema") or "dbo"
            target_table_name = fk.get("target_table")
            target_col_name = fk.get("target_column")
            
            if not source_table_name or not source_col_name or not target_table_name or not target_col_name:
                continue
            
            # 4.1 Find Source Table
            src_table_ref = None
            for (s_name, t_name), t_ref in table_refs.items():
                if t_name.lower() == source_table_name.lower():
                    src_table_ref = t_ref
                    break
            
            if not src_table_ref:
                print(f"  [Warning] Inferred FK source table '{source_table_name}' not found. Skipping.")
                continue

            # 4.2 Find Target Table
            tgt_table_ref = None
            for (s_name, t_name), t_ref in table_refs.items():
                if t_name.lower() == target_table_name.lower():
                    tgt_table_ref = t_ref
                    break
            
            if not tgt_table_ref:
                print(f"  [Warning] Inferred FK target table '{target_table_name}' not found. Skipping.")
                continue

            # 4.3 Validate Source Column exists
            src_col_exists = any(c.get("column_name", "").lower() == source_col_name.lower() for c in src_table_ref.get("columns", []))
            if not src_col_exists:
                print(f"  [Warning] Inferred FK source column '{source_col_name}' not found in '{source_table_name}'. Skipping.")
                continue

            # 4.4 Validate Target Column exists and acts as a PK
            tgt_col_is_pk = False
            for c in tgt_table_ref.get("columns", []):
                if c.get("column_name", "").lower() == target_col_name.lower():
                    if c.get("primary_key") or c.get("inferred_pk"):
                        tgt_col_is_pk = True
                    break
            
            if not tgt_col_is_pk:
                print(f"  [Warning] Inferred FK target '{target_table_name}.{target_col_name}' is not a PK. Skipping.")
                continue

            # 4.5 Apply FK ONLY if not already marked natively
            for c in src_table_ref.get("columns", []):
                if c.get("column_name", "").lower() == source_col_name.lower():
                    if not c.get("foreign_key"):
                        c["inferred_fk"] = True
                        c["inferred_fk_references"] = {
                            "schema": target_schema_name,
                            "table": target_table_name,
                            "column": target_col_name
                        }
                    break
                        
    print("--- Completed AI PK/FK Inference ---")
    return metadata
