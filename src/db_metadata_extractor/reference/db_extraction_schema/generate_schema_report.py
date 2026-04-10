#!/usr/bin/env python3
"""
Database Schema Intelligence Report Generator
=============================================
Generates a rich, interactive HTML report from a database schema JSON file.

Usage:
    python generate_schema_report.py --input schema.json
    python generate_schema_report.py --input schema.json --output my_report.html
    python generate_schema_report.py --input schema.json --output report.html --title "My DB Report"

JSON Schema Expected Format:
    {
      "source": {
        "db_type": "postgres",
        "database": "retail_analytics",
        "schema": "public",
        "extracted_at": "2026-02-26T23:12:06Z"
      },
      "schemas": [
        {
          "schema_name": "public",
          "tables": [
            {
              "table_name": "dim_brand",
              "table_type": "BASE TABLE",
              "row_count": 5000,
              "size": "1.24MB",
              "description": "...",
              "columns": [
                {
                  "column_name": "brand_id",
                  "data_type": "integer",
                  "nullable": false,
                  "primary_key": true,
                  "foreign_key": false,
                  "ai_description": "..."
                }
              ]
            }
          ]
        }
      ]
    }
"""

import json
import argparse
import sys
from pathlib import Path
from datetime import datetime


# ── Field Normalizer ──────────────────────────────────────────────────────────

def normalize_tables(tables: list) -> list:
    """
    Normalize column field names so the script works with multiple JSON formats.

    Supported field aliases:
      Column description : 'ai_description', 'column_description', 'description'
      Primary key        : 'primary_key', 'is_primary_key'
      Foreign key        : 'foreign_key', 'is_foreign_key'
      Nullable           : 'nullable' (bool) OR 'is_nullable' (str "YES"/"NO")
      Table description  : 'ai_description', 'description'
      Distinct count     : 'distinct_count'
      FK reference       : 'references' dict with keys 'table', 'schema', 'column'
    """
    normalized = []
    for table in tables:
        t = dict(table)

        # Normalize table-level description (support ai_description)
        if not t.get("description"):
            t["description"] = t.get("ai_description") or ""

        norm_cols = []
        for col in t.get("columns", []):
            if isinstance(col, dict):
                c = dict(col)
            elif isinstance(col, (list, tuple)):
                c = {
                    "column_name":           col[0] if len(col) > 0 else None,
                    "data_type":             col[1] if len(col) > 1 else None,
                    "nullable":              col[2] if len(col) > 2 else None,
                    "is_generated":          col[3] if len(col) > 3 else None,
                    "primary_key":           col[4] if len(col) > 4 else None,
                    "foreign_key":           col[5] if len(col) > 5 else None,
                    "foreign_key_reference": col[6] if len(col) > 6 else None,
                }
                if len(col) >= 9:
                    c["description"]    = col[7]
                    c["distinct_count"] = col[8]
                elif len(col) == 8:
                    if isinstance(col[7], (int, float)):
                        c["distinct_count"] = col[7]
                    else:
                        c["description"]    = col[7]
                        c["distinct_count"] = None
                else:
                    c["distinct_count"] = None
            else:
                continue

            # ── is_primary_key: prefer 'primary_key' (raw JSON), fall back to 'is_primary_key' ──
            if "primary_key" in c:
                raw_pk = c["primary_key"]
            else:
                raw_pk = c.get("is_primary_key")
            if isinstance(raw_pk, bool):
                c["is_primary_key"] = raw_pk
            elif isinstance(raw_pk, (int, float)):
                c["is_primary_key"] = bool(raw_pk)
            elif isinstance(raw_pk, str):
                c["is_primary_key"] = raw_pk.strip().lower() in ("true", "yes", "1")
            else:
                c["is_primary_key"] = False

            # ── is_foreign_key: prefer 'foreign_key' (raw JSON), fall back to 'is_foreign_key' ──
            if "foreign_key" in c:
                raw_fk = c["foreign_key"]
            else:
                raw_fk = c.get("is_foreign_key")
            if isinstance(raw_fk, bool):
                c["is_foreign_key"] = raw_fk
            elif isinstance(raw_fk, (int, float)):
                c["is_foreign_key"] = bool(raw_fk)
            elif isinstance(raw_fk, str):
                c["is_foreign_key"] = raw_fk.strip().lower() in ("true", "yes", "1")
            else:
                c["is_foreign_key"] = False

            # ── FIX: Extract explicit reference target from 'references' dict if present ──
            # e.g. "references": {"schema": "public", "table": "dimcustomer", "column": "customerid"}
            ref = c.get("references")
            if isinstance(ref, dict):
                c["fk_ref_table"]  = ref.get("table")
                c["fk_ref_column"] = ref.get("column")
                c["fk_ref_schema"] = ref.get("schema")
            else:
                c.setdefault("fk_ref_table",  None)
                c.setdefault("fk_ref_column", None)
                c.setdefault("fk_ref_schema", None)

            # nullable (bool) → is_nullable (string "YES"/"NO")
            if "is_nullable" not in c:
                raw = c.get("nullable")
                if isinstance(raw, bool):
                    c["is_nullable"] = "YES" if raw else "NO"
                elif isinstance(raw, str):
                    c["is_nullable"] = "YES" if raw.upper() in ("YES", "TRUE", "1") else "NO"
                else:
                    c["is_nullable"] = ""
            else:
                raw = c["is_nullable"]
                if isinstance(raw, bool):
                    c["is_nullable"] = "YES" if raw else "NO"

            # description: prefer ai_description → column_description → description
            c["description"] = (
                c.get("ai_description")
                or c.get("column_description")
                or c.get("description")
                or ""
            )

            # ── Inferred Flags ──
            c["inferred_pk"] = bool(c.get("inferred_pk"))
            c["inferred_fk"] = bool(c.get("inferred_fk"))
            ref_inf = c.get("inferred_fk_references")
            if isinstance(ref_inf, dict):
                c["inf_ref_table"]  = ref_inf.get("table")
                c["inf_ref_column"] = ref_inf.get("column")
                c["inf_ref_schema"] = ref_inf.get("schema")
            else:
                c.setdefault("inf_ref_table",  None)
                c.setdefault("inf_ref_column", None)
                c.setdefault("inf_ref_schema", None)

            c.setdefault("distinct_count", None)
            norm_cols.append(c)

        t["columns"] = norm_cols
        normalized.append(t)

    return normalized


# ── Relationship Inference ─────────────────────────────────────────────────────

def infer_relationships(tables: list) -> list:
    """
    Detect relationships between tables.

    Resolution priority per FK column:
      1. Explicit 'references.table' in the JSON  →  use directly (most reliable).
      2. Explicit 'foreign_key: true' flag         →  try name-based matching.
      3. Naming convention (_id / id suffix, not PK, not already FK-flagged)
                                                   →  try name-based matching.

    Unresolvable FKs (is_foreign_key=True but no target found) are stored with
    source==target and unresolved=True so they are still counted in stats but
    the JS graph filters them out (no self-loop arrows drawn).
    """
    relationships: list[dict] = []
    seen: set = set()
    table_by_name = {t["table_name"]: t for t in tables}

    # Pre-compute primary-key column name per table
    pk_by_table: dict[str, str | None] = {}
    for t in tables:
        pk_name = None
        for c in t.get("columns", []):
            if c.get("is_primary_key"):
                pk_name = c.get("column_name")
                if pk_name:
                    break
        pk_by_table[t["table_name"]] = pk_name

    table_names = set(table_by_name.keys())

    def _find_target_by_name(cname: str, src: str) -> str | None:
        """
        Heuristic name-based lookup.
        Strips _id / id suffix then tries exact + prefixed table name variants.
        """
        prefixes: list[str] = []

        # Handle both  brand_id  (strip _id → brand)
        # and          customerid (strip id  → customer)
        if cname.endswith("_id"):
            prefixes.append(cname[:-3])
        elif cname.endswith("id") and len(cname) > 2:
            prefixes.append(cname[:-2])

        if cname.endswith("_key"):
            prefixes.append(cname[:-4])

        # Also try the raw column name in case table name matches exactly
        if not prefixes:
            prefixes.append(cname)

        table_prefixes = ("dim_", "fact_", "vw_", "stg_", "int_", "lkp_")

        for prefix in prefixes:
            for candidate in table_names:
                if candidate == src:
                    continue

                # Strip known table-name prefixes for comparison
                stripped = candidate
                for pfx in table_prefixes:
                    if stripped.startswith(pfx):
                        stripped = stripped[len(pfx):]
                        break

                if (
                    candidate == prefix
                    or stripped  == prefix
                    or candidate == f"dim_{prefix}"
                    or candidate == f"fact_{prefix}"
                    or candidate == f"stg_{prefix}"
                    or candidate == f"lkp_{prefix}"
                ):
                    return candidate
        return None

    for table in tables:
        src      = table["table_name"]
        rel_type = "FK"

        for col in table.get("columns", []):
            cname = col.get("column_name")
            if not cname:
                continue

            # Primary keys are never FK references
            if col.get("is_primary_key"):
                continue

            is_fk_flag     = bool(col.get("is_foreign_key"))
            uses_id_suffix = (
                str(cname).endswith("_id")
                or str(cname).endswith("id")   # covers customerid, productid, etc.
            )

            if not is_fk_flag and not uses_id_suffix:
                continue

            # ── Resolution step 0: use AI inferred references if present ──
            inf_ref_table = col.get("inf_ref_table")
            explicit_ref_table = col.get("fk_ref_table")
            
            is_inferred = False
            if inf_ref_table and inf_ref_table in table_names:
                target = inf_ref_table
                target_col_name = col.get("inf_ref_column") or pk_by_table.get(target)
                is_inferred = True
            elif explicit_ref_table and explicit_ref_table in table_names:
                # ── Resolution step 1: use explicit 'references.table' if present ──
                target          = explicit_ref_table
                target_col_name = col.get("fk_ref_column") or pk_by_table.get(target)
            else:
                # ── Resolution step 2 & 3: heuristic name matching ──
                target          = _find_target_by_name(cname, src)
                target_col_name = pk_by_table.get(target) if target else None

            if target and target != src:
                key = (src, target, cname)
                if key not in seen:
                    seen.add(key)
                    relationships.append({
                        "source":        src,
                        "target":        target,
                        "type":          rel_type,
                        "source_column": cname,
                        "target_column": target_col_name,
                        "unresolved":    False,
                        "is_inferred":   is_inferred or bool(col.get("inferred_fk"))
                    })
            elif is_fk_flag or col.get("inferred_fk"):
                # FK-flagged but target couldn't be resolved → record as unresolved
                key = (src, src, cname)
                if key not in seen:
                    seen.add(key)
                    relationships.append({
                        "source":        src,
                        "target":        src,
                        "type":          rel_type,
                        "source_column": cname,
                        "target_column": None,
                        "unresolved":    True,
                        "is_inferred":   bool(col.get("inferred_fk"))
                    })

    return relationships


# ── Stats Computation ──────────────────────────────────────────────────────────

def compute_stats(tables: list, relationships: list) -> dict:
    total_cols  = sum(len(t.get("columns", [])) for t in tables)
    total_rows  = sum(t.get("row_count") or 0 for t in tables)
    base_tables = [t for t in tables if t.get("table_type") == "BASE TABLE"]
    # Counting TABLES with PKs/FKs for accurate coverage stats
    pk_tables     = sum(1 for t in tables if any(c.get("is_primary_key") for c in t.get("columns", [])))
    inf_pk_tables = sum(1 for t in tables if not any(c.get("is_primary_key") for c in t.get("columns", [])) and any(c.get("inferred_pk") for c in t.get("columns", [])))

    fk_tables     = sum(1 for t in tables if any(c.get("is_foreign_key") for c in t.get("columns", [])))
    inf_fk_tables = sum(1 for t in tables if not any(c.get("is_foreign_key") for c in t.get("columns", [])) and any(c.get("inferred_fk") for c in t.get("columns", [])))

    avg_cols = round(total_cols / len(tables), 1) if tables else 0

    # fk_relations: resolved (non-self-loop) FK edges drawn in the graph
    fk_relations = len([
        r for r in relationships
        if r["type"] == "FK" and r.get("source") != r.get("target") and not r.get("is_inferred")
    ])
    inf_fk_relations = len([
        r for r in relationships
        if r["type"] == "FK" and r.get("source") != r.get("target") and r.get("is_inferred")
    ])

    return {
        "total_tables":       len(tables),
        "base_tables":        len(base_tables),
        "total_columns":      total_cols,
        "avg_cols_per_table": avg_cols,
        "primary_keys":       pk_tables,
        "inferred_pks":       inf_pk_tables,
        "foreign_key_cols":   fk_tables,
        "inferred_fks":       inf_fk_tables,
        "fk_relations":       fk_relations,
        "inferred_fk_rels":   inf_fk_relations,
        "total_rows":         total_rows,
        "total_rows_fmt":     f"{total_rows / 1e6:.2f}M" if total_rows >= 1_000_000 else f"{total_rows:,}",
    }


# ── HTML Template ──────────────────────────────────────────────────────────────

def build_html(
    source: dict,
    tables: list,
    relationships: list,
    stats: dict,
    title: str,
    db_type: str,
    database: str,
    schema_name: str,
    extracted_fmt: str,
) -> str:
    tables_json        = json.dumps(tables, indent=2)
    relationships_json = json.dumps(relationships, indent=2)
    source_json        = json.dumps(source, indent=2)
    stats_json         = json.dumps(stats, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');

:root {{
  --bg: #f5f7ff;
  --surface: #ffffff;
  --surface2: #eef3ff;
  --border: #c8d2f0;
  --accent: #2563eb;
  --accent2: #16a34a;
  --accent3: #4f46e5;
  --accent4: #f97316;
  --accent5: #dc2626;
  --text: #111827;
  --text2: #4b5563;
  --text3: #6b7280;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'IBM Plex Sans', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; overflow-x: hidden; }}

/* Header */
.header {{
  background: linear-gradient(135deg, #e0ecff 0%, #c0d8ff 100%);
  border-bottom: 1px solid var(--border);
  padding: 28px 40px 20px;
  position: relative; overflow: hidden;
}}
.header::before {{
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse 60% 80% at 20% 50%, rgba(88,166,255,0.06) 0%, transparent 70%);
}}
.header-top {{ display: flex; align-items: center; justify-content: space-between; position: relative; }}
.header-badge {{
  display: inline-flex; align-items: center; gap: 8px;
  background: rgba(88,166,255,0.1); border: 1px solid rgba(88,166,255,0.3);
  border-radius: 6px; padding: 4px 12px; font-size: 11px;
  color: var(--accent); font-family: 'IBM Plex Mono', monospace; letter-spacing: 0.05em;
}}
.header-badge::before {{ content: '●'; color: var(--accent2); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
.header h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.5px; margin-top: 14px; position: relative; }}
.header h1 span {{ color: var(--accent); }}
.header-meta {{ color: var(--text2); font-size: 13px; margin-top: 6px; font-family: 'IBM Plex Mono', monospace; position: relative; }}
.header-meta em {{ color: var(--text3); margin: 0 8px; font-style: normal; }}

/* Tiles */
.tiles-section {{ padding: 28px 40px; }}
.tiles-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; }}
.tile {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px 18px; position: relative; overflow: hidden;
  transition: border-color .2s, transform .2s; cursor: default;
}}
.tile:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
.tile::after {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--tile-color, var(--accent)); }}
.tile-value {{ font-size: 32px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; line-height: 1; }}
.tile-label {{ font-size: 11px; color: var(--text2); margin-top: 8px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }}
.tile-sub {{ font-size: 11px; color: var(--text3); margin-top: 4px; font-family: 'IBM Plex Mono', monospace; }}

/* Section Title */
.section-title {{
  font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--accent);
  padding: 0 40px 14px; display: flex; align-items: center; gap: 10px;
}}
.section-title::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}

/* Graph */
.graph-section {{ padding: 0 40px 32px; }}
.graph-container {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
.graph-toolbar {{ padding: 12px 18px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.graph-toolbar-label {{ font-size: 12px; color: var(--text2); font-weight: 600; }}
.legend-item {{
  display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text2);
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 20px; padding: 4px 10px; cursor: pointer; transition: all .2s;
}}
.legend-item.active {{ color: var(--text); border-color: var(--accent); }}
.legend-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
.btn {{
  padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--surface2); color: var(--text2); font-size: 12px;
  cursor: pointer; font-family: 'IBM Plex Sans', sans-serif; transition: all .15s;
}}
.btn:hover {{ border-color: var(--accent); color: var(--accent); }}
.btn-primary {{ background: rgba(88,166,255,0.1); border-color: rgba(88,166,255,0.4); color: var(--accent); }}
.btn-primary:hover {{ background: rgba(88,166,255,0.2); }}
.select-filter {{
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text); padding: 5px 10px; border-radius: 6px;
  font-size: 12px; font-family: 'IBM Plex Sans', sans-serif; cursor: pointer;
}}
.select-filter:focus {{ outline: none; border-color: var(--accent); }}
.graph-body {{ display: flex; height: 480px; }}
.graph-legend-bar {{
  padding: 8px 18px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  background: var(--surface2); font-size: 11px; color: var(--text2);
}}
.graph-legend-bar .legend-symbol {{
  display: inline-flex; align-items: center; gap: 5px;
  font-family: 'IBM Plex Mono', monospace;
}}
.graph-legend-bar .legend-symbol .sym {{
  font-size: 14px; line-height: 1;
}}
.graph-legend-bar .legend-line {{
  display: inline-flex; align-items: center; gap: 5px;
}}
.graph-legend-bar .legend-line .line-sample {{
  width: 24px; height: 2px; display: inline-block;
}}
#graph-canvas {{ flex: 1; }}
.graph-info-panel {{
  width: 240px; border-left: 1px solid var(--border);
  background: var(--surface2); padding: 18px; overflow-y: auto;
  display: flex; flex-direction: column; gap: 10px;
}}
.info-panel-title {{ font-size: 11px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text2); margin-bottom: 4px; }}
.info-panel-empty {{ color: var(--text3); font-size: 12px; line-height: 1.6; }}
.info-node-name {{ font-size: 16px; font-weight: 700; color: var(--accent); }}
.info-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; font-family: 'IBM Plex Mono', monospace; }}
.info-stat {{ display: flex; justify-content: space-between; font-size: 12px; padding: 4px 0; border-bottom: 1px solid var(--border); }}
.info-stat:last-child {{ border: none; }}
.info-stat-label {{ color: var(--text2); }}
.info-stat-val {{ font-family: 'IBM Plex Mono', monospace; color: var(--text); font-weight: 500; }}
.info-desc {{ font-size: 11px; line-height: 1.65; color: var(--text2); margin-top: 6px; }}
.related-table-list {{ display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }}
.related-table-chip {{
  font-size: 11px; padding: 4px 8px; background: var(--bg);
  border: 1px solid var(--border); border-radius: 4px;
  font-family: 'IBM Plex Mono', monospace; color: var(--accent3);
  cursor: pointer; transition: border-color .15s;
}}
.related-table-chip:hover {{ border-color: var(--accent3); }}

/* Tables */
.tables-section {{ padding: 0 40px 48px; }}
.search-filter-bar {{ display: flex; align-items: center; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }}
.search-input {{
  flex: 1; min-width: 200px; background: var(--surface); border: 1px solid var(--border);
  color: var(--text); padding: 8px 14px; border-radius: 8px;
  font-size: 13px; font-family: 'IBM Plex Sans', sans-serif; transition: border-color .15s;
}}
.search-input:focus {{ outline: none; border-color: var(--accent); }}
.search-input::placeholder {{ color: var(--text3); }}
.filter-chips {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.filter-chip {{
  padding: 5px 12px; border-radius: 20px; border: 1px solid var(--border);
  font-size: 12px; cursor: pointer; background: var(--surface); color: var(--text2); transition: all .15s;
}}
.filter-chip.active {{ background: rgba(88,166,255,0.1); border-color: var(--accent); color: var(--accent); }}
.filter-chip:hover {{ border-color: var(--accent); }}
.table-cards {{ display: flex; flex-direction: column; gap: 12px; }}
.table-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; transition: border-color .2s, box-shadow .2s; }}
.table-card:hover {{ border-color: rgba(37,99,235,0.4); box-shadow: 0 6px 12px rgba(15,23,42,0.08); }}
.table-card-header {{ display: flex; align-items: center; gap: 12px; padding: 14px 18px; cursor: pointer; transition: background .15s; }}
.table-card-header:hover {{ background: rgba(255,255,255,0.02); }}
.table-type-badge {{ font-size: 10px; font-family: 'IBM Plex Mono', monospace; font-weight: 600; padding: 2px 7px; border-radius: 3px; flex-shrink: 0; }}
.type-base {{ background: rgba(88,166,255,0.15); color: var(--accent); border: 1px solid rgba(88,166,255,0.3); }}
.table-name {{ font-family: 'IBM Plex Mono', monospace; font-size: 14px; font-weight: 600; color: var(--accent3); }}
.table-meta {{ margin-left: auto; display: flex; gap: 18px; align-items: center; }}
.table-meta-item {{ font-size: 12px; color: var(--text2); font-family: 'IBM Plex Mono', monospace; }}
.table-meta-item span {{ color: var(--text); font-weight: 500; }}
.expand-icon {{ color: var(--text3); font-size: 14px; transition: transform .2s; margin-left: 8px; }}
.table-card.expanded .expand-icon {{ transform: rotate(180deg); }}
.table-card-body {{ border-top: 1px solid var(--border); display: none; padding: 18px; }}
.table-card.expanded .table-card-body {{ display: block; }}
.table-desc {{
  font-size: 13px; line-height: 1.7; color: var(--text2);
  background: var(--surface2); border-left: 3px solid var(--accent);
  padding: 12px 14px; border-radius: 0 6px 6px 0; margin-bottom: 16px;
}}
.columns-table {{ width: 100%; border-collapse: collapse; }}
.columns-table th {{
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text3); padding: 8px 12px; text-align: left;
  border-bottom: 1px solid var(--border); background: var(--surface2);
}}
.columns-table td {{ padding: 9px 12px; font-size: 12px; border-bottom: 1px solid rgba(200,210,240,0.5); vertical-align: top; }}
.columns-table tr:last-child td {{ border: none; }}
.columns-table tr:hover td {{ background: rgba(238,243,255,0.6); }}
.col-name {{ font-family: 'IBM Plex Mono', monospace; color: var(--accent3); font-weight: 500; }}
.col-type {{ font-family: 'IBM Plex Mono', monospace; color: var(--accent4); font-size: 11px; }}
.col-pk {{ font-family: 'IBM Plex Mono', monospace; font-size: 10px; padding: 1px 5px; background: rgba(255,166,87,0.15); color: var(--accent4); border-radius: 3px; }}
.col-fk {{ font-family: 'IBM Plex Mono', monospace; font-size: 10px; padding: 1px 5px; background: rgba(210,168,255,0.15); color: var(--accent3); border-radius: 3px; margin-left: 3px; }}
.col-nullable {{ font-size: 10px; color: var(--text3); }}
.col-distinct {{ font-size: 11px; color: var(--text3); font-family: 'IBM Plex Mono', monospace; }}
.col-desc {{ color: var(--text2); font-size: 12px; line-height: 1.5; }}
.no-results {{ color: var(--text3); padding: 20px; font-size: 13px; }}

/* Footer */
.footer {{ padding: 24px 40px; border-top: 1px solid var(--border); color: var(--text3); font-size: 12px; font-family: 'IBM Plex Mono', monospace; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }}
</style>
</head>
<body>

<!-- ── DATA LAYER ── -->
<script>
const TABLES = {tables_json};
const RELATIONSHIPS = {relationships_json};
const SOURCE = {source_json};
const STATS = {stats_json};
</script>

<!-- ── HEADER ── -->
<div class="header">
  <div class="header-top">
    <div class="header-badge">SCHEMA INTELLIGENCE REPORT</div>
    <div style="display:flex;gap:8px;">
      <button class="btn" onclick="exportJSON()">⬇ Export JSON</button>
      <button class="btn btn-primary" onclick="window.print()">⎙ Print</button>
    </div>
  </div>
  <h1>📦 <span>{title}</span></h1>
  <div class="header-meta">
    <span>{db_type}://{database}</span>
    <em>·</em>
    <span>schema: {schema_name}</span>
    <em>·</em>
    <span>extracted {extracted_fmt}</span>
  </div>
</div>

<!-- ── TILES ── -->
<div class="tiles-section">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
    <div style="flex:1"></div>
    <div style="display:flex; align-items:center; gap:10px;">
      <span class="graph-toolbar-label">Filter Table:</span>
      <select class="select-filter" id="tiles-filter" onchange="onTilesFilterChange()">
        <option value="ALL">All Tables</option>
      </select>
    </div>
  </div>
  <div class="tiles-grid" id="tiles-grid"></div>
</div>

<!-- ── RELATIONSHIP GRAPH ── -->
<div class="section-title">Table – Relationship Graph</div>
<div class="graph-section">
  <div class="graph-container">
    <div class="graph-toolbar">
      <span class="graph-toolbar-label">Edges:</span>
      <div class="legend-item active" id="leg-fk" onclick="toggleEdge('FK', this)">
        <div class="legend-dot" style="background:#7fb3f5"></div> FK Constraint
      </div>
      <div class="legend-item" id="leg-col" onclick="toggleColumns(this)" style="margin-left:8px;">
        <div class="legend-dot" style="background:#e1c4ff"></div> Columns: OFF
      </div>
      <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
        <button class="btn" id="freeze-btn" onclick="toggleFreeze()">🧊 Freeze</button>
        <button class="btn" onclick="fitGraph()">⊞ Fit</button>
        <span class="graph-toolbar-label">Highlight:</span>
        <select class="select-filter" id="graph-filter" onchange="filterGraph()">
          <option value="">All Tables</option>
        </select>
        <button class="btn" onclick="resetGraph()">↺ Reset</button>
      </div>
    </div>
    <div class="graph-legend-bar">
      <span style="font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;">Legend:</span>
      <span class="legend-symbol"><span class="sym" style="color:#7fb3f5;">●</span> Table</span>
      <span class="legend-symbol"><span class="sym" style="color:#e1c4ff;">◇</span> Column</span>
      <span class="legend-symbol"><span class="sym" style="color:#ffc691;">◈</span> Referenced Table</span>
      <span class="legend-line"><span class="line-sample" style="background:#7fb3f5;"></span> FK Edge</span>
      <span class="legend-line"><span class="line-sample" style="border-bottom: 2px dashed #7fb3f5; transform:translateY(-4px)"></span> Inferred FK</span>
      <span class="legend-line"><span class="line-sample" style="background:#ffc691;"></span> FK Reference</span>
      <span class="legend-line"><span class="line-sample" style="background:#e1c4ff;"></span> Column Link</span>
    </div>
    <div class="graph-body">
      <svg id="graph-canvas"></svg>
      <div class="graph-info-panel" id="info-panel">
        <div class="info-panel-title">Node Info</div>
        <div class="info-panel-empty">Click any node to see details about the table and its relationships.</div>
      </div>
    </div>
  </div>
</div>

<!-- ── TABLE DETAILS ── -->
<div class="section-title">Table Details</div>
<div class="tables-section">
  <div class="search-filter-bar">
    <input class="search-input" type="text" id="search-input" placeholder="Search tables, columns or descriptions…" oninput="filterTables()">
    <div class="filter-chips">
      <div class="filter-chip active" onclick="setTypeFilter('ALL', this)">All</div>
      <div class="filter-chip" onclick="setTypeFilter('BASE TABLE', this)">Base Tables</div>
    </div>
  </div>
  <div class="table-cards" id="table-cards"></div>
</div>

<div class="footer">
  <span>Source: {db_type}://{database} · {schema_name} · {extracted_fmt}</span>
  <span>Generated by Schema Intelligence Reporter</span>
</div>

<!-- ── JAVASCRIPT ── -->
<script>
// ── Tiles and Filters ──────────────────────────────────────────────────────
function renderTiles(targetStats = STATS) {{
  const TILE_DEFS = [
    {{ value: targetStats.total_tables,       label: 'Total Tables',   sub: (targetStats.base_tables || 0) + ' base' }},
    {{ value: targetStats.total_columns,      label: 'Total Columns',  sub: targetStats.avg_cols_per_table ? 'avg ' + targetStats.avg_cols_per_table + ' / table' : 'columns count', color: '#d2a8ff' }},
    {{
      value: `${{targetStats.primary_keys}} <span style="font-size:18px; color:var(--text3); font-weight:400">/ ${{targetStats.inferred_pks || 0}}</span>`,
      label: 'Primary Keys',
      sub: 'Native / Inferred',
      color: '#ffa657'
    }},
    {{
      value: `${{targetStats.foreign_key_cols}} <span style="font-size:18px; color:var(--text3); font-weight:400">/ ${{targetStats.inferred_fks || 0}}</span>`,
      label: 'FK Columns',
      sub: 'Native / Inferred',
      color: '#3fb950'
    }},
    {{ value: targetStats.total_rows_fmt,     label: 'Total Rows',     sub: targetStats.avg_cols_per_table ? 'estimated across tables' : 'estimated count', color: '#ff7b72' }},
  ];

  const tg = document.getElementById('tiles-grid');
  tg.innerHTML = '';
  TILE_DEFS.forEach(t => {{
    tg.innerHTML += `<div class="tile" style="--tile-color:${{t.color}}">
      <div class="tile-value" style="color:${{t.color}}">${{t.value}}</div>
      <div class="tile-label">${{t.label}}</div>
      ${{t.sub ? `<div class="tile-sub">${{t.sub}}</div>` : ''}}
    </div>`;
  }});
}}

function onTilesFilterChange() {{
  const val = document.getElementById('tiles-filter').value;
  if (val === 'ALL') {{
    renderTiles(STATS);
    return;
  }}

  const t = TABLES.find(tbl => tbl.table_name === val);
  if (!t) return;

  const pks = t.columns.filter(c => c.is_primary_key).length;
  const fks = t.columns.filter(c => c.is_foreign_key).length;
  const rows = t.row_count || 0;
  const rowsFmt = rows >= 1000000 ? (rows / 1000000).toFixed(2) + 'M' : rows.toLocaleString();

  renderTiles({{
    total_tables: 1,
    base_tables: t.table_type === 'BASE TABLE' ? 1 : 0,
    total_columns: t.columns.length,
    avg_cols_per_table: null,
    primary_keys: pks,
    foreign_key_cols: fks,
    total_rows_fmt: rowsFmt
  }});
}}

// ── Dropdown Population ───────────────────────────────────────────────────
const gf = document.getElementById('graph-filter');
const tf = document.getElementById('tiles-filter');
TABLES.forEach(t => {{
  const o = document.createElement('option');
  o.value = t.table_name;
  o.textContent = t.table_name;
  gf.appendChild(o.cloneNode(true));
  tf.appendChild(o.cloneNode(true));
}});

// ── D3 Relationship Graph ─────────────────────────────────────────────────
let showFK = true;
let showColumns = false;
let isFrozen = false;
const NODE_COLORS = {{ 'BASE TABLE': '#2563eb', 'TABLE': '#2563eb', 'COLUMN': '#7c3aed', 'REF_TABLE': '#ea580c' }};

const svgEl = d3.select('#graph-canvas');
const graphBody = document.querySelector('.graph-body');
let GW = graphBody.clientWidth - 240, GH = 480;
svgEl.attr('width', GW).attr('height', GH);

const gRoot = svgEl.append('g');
svgEl.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', e => gRoot.attr('transform', e.transform)));

// Arrow markers
const defs = svgEl.append('defs');
[['FK','#2563eb'],['COL','#7c3aed'],['FK_REF','#ea580c']].forEach(([type, color]) => {{
  defs.append('marker')
    .attr('id', 'arrow-' + type)
    .attr('viewBox', '0 -5 10 10').attr('refX', 24).attr('refY', 0)
    .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
    .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', color).attr('opacity', 0.8);
}});

let nodes = [];
let allLinks = [];

function buildOverviewGraphData() {{
  nodes = TABLES.map(t => ({{
    id: t.table_name,
    type: t.table_type || 'BASE TABLE',
    kind: 'TABLE',
    table: t.table_name,
    column: null,
  }}));

  // FIX 3: Filter out self-loops (unresolved FKs) from the visible graph
  allLinks = RELATIONSHIPS
    .filter(r => r.source !== r.target)
    .map(r => ({{
      source: r.source,
      target: r.target,
      type: r.type,
      is_inferred: !!r.is_inferred
    }}));
}}

function buildDetailGraphData(focusTable) {{
  const tableMap = new Map(TABLES.map(t => [t.table_name, t]));
  const rels = RELATIONSHIPS || [];
  const relatedTables = new Set();
  relatedTables.add(focusTable);

  rels.forEach(r => {{
    if (r.source === r.target) return;
    if (r.source === focusTable || r.target === focusTable) {{
      relatedTables.add(r.source);
      relatedTables.add(r.target);
    }}
  }});

  // Also add tables referenced via FK columns
  const focusTbl = tableMap.get(focusTable);
  if (focusTbl) {{
    (focusTbl.columns || []).forEach(col => {{
      if (col.fk_ref_table) relatedTables.add(col.fk_ref_table);
    }});
  }}
  // And tables that reference the focus table
  TABLES.forEach(t => {{
    (t.columns || []).forEach(col => {{
      if (col.fk_ref_table === focusTable) relatedTables.add(t.table_name);
    }});
  }});

  nodes = [];
  allLinks = [];
  const nodeById = new Map();

  function ensureTableNode(tName, isRef) {{
    if (!tName || nodeById.has(tName)) return;
    const t = tableMap.get(tName);
    const n = {{
      id: tName,
      type: isRef ? 'REF_TABLE' : (t ? (t.table_type || 'BASE TABLE') : 'BASE TABLE'),
      kind: 'TABLE',
      table: tName,
      column: null,
    }};
    nodes.push(n);
    nodeById.set(tName, n);
  }}

  function addColumnNode(tName, col) {{
    if (!col || !col.column_name) return;
    const colId = `${{tName}}.${{col.column_name}}`;
    if (nodeById.has(colId)) return;
    const n = {{
      id: colId,
      type: 'COLUMN',
      kind: 'COLUMN',
      table: tName,
      column: col.column_name,
      is_primary_key: !!col.is_primary_key,
      is_foreign_key: !!col.is_foreign_key,
    }};
    nodes.push(n);
    nodeById.set(colId, n);
    allLinks.push({{ source: tName, target: colId, type: 'COL' }});
  }}

  // Add table and column nodes for related tables
  relatedTables.forEach(tName => {{
    const isFocusOrDirect = (tName === focusTable);
    ensureTableNode(tName, !isFocusOrDirect && !rels.some(r => (r.source === tName || r.target === tName) && (r.source === focusTable || r.target === focusTable)));
    const t = tableMap.get(tName);
    if (!t) return;
    (t.columns || []).forEach(col => addColumnNode(tName, col));
  }});

  // Add FK relationship edges (column-to-column when possible)
  rels.forEach(r => {{
    if (r.source === r.target) return;
    if (!relatedTables.has(r.source) && !relatedTables.has(r.target)) return;
    const srcCol = r.source_column ? `${{r.source}}.${{r.source_column}}` : null;
    const tgtCol = r.target_column ? `${{r.target}}.${{r.target_column}}` : null;

    if (srcCol && tgtCol && nodeById.has(srcCol) && nodeById.has(tgtCol)) {{
      allLinks.push({{ source: srcCol, target: tgtCol, type: 'FK_REF', is_inferred: !!r.is_inferred }});
    }} else {{
      ensureTableNode(r.source, false);
      ensureTableNode(r.target, false);
      allLinks.push({{ source: r.source, target: r.target, type: r.type, is_inferred: !!r.is_inferred }});
    }}
  }});
}}

buildOverviewGraphData();

const sim = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(allLinks).id(d => d.id).distance(130))
  .force('charge', d3.forceManyBody().strength(-300))
  .force('center', d3.forceCenter(GW / 2, GH / 2))
  .force('collide', d3.forceCollide(42));

let linkSel = gRoot.append('g').selectAll('line');
let nodeSel = gRoot.append('g').selectAll('g');

function renderGraph() {{
  const visNodes = showColumns ? nodes : nodes.filter(n => n.kind === 'TABLE');
  let visLinks = [];

  if (showColumns) {{
    visLinks = allLinks.filter(l => {{
      if (l.type === 'COL') return true;
      if ((l.type === 'FK' || l.type === 'FK_REF') && showFK) return true;
      return l.type !== 'FK' && l.type !== 'FK_REF' && l.type !== 'COL';
    }});
  }} else {{
    // Collapse column links into table links
    const tableLinks = new Map();
    allLinks.forEach(l => {{
      const isFk = (l.type === 'FK' || l.type === 'FK_REF');
      if (!isFk || !showFK) return;

      const srcNode = typeof l.source === 'string' ? nodes.find(n => n.id === l.source) : l.source;
      const tgtNode = typeof l.target === 'string' ? nodes.find(n => n.id === l.target) : l.target;
      if (!srcNode || !tgtNode) return;

      const sTbl = srcNode.table;
      const tTbl = tgtNode.table;
      if (sTbl === tTbl) return;

      const key = `${{sTbl}}-${{tTbl}}`;
      if (!tableLinks.has(key)) {{
        tableLinks.set(key, {{ source: sTbl, target: tTbl, type: 'FK', is_inferred: !!l.is_inferred }});
      }}
    }});
    visLinks = Array.from(tableLinks.values());
  }}

  linkSel = linkSel.data(visLinks, d => {{
    const s = d.source.id || d.source;
    const t = d.target.id || d.target;
    return s + '-' + t;
  }});
  linkSel.exit().remove();
  linkSel = linkSel.enter().append('line')
    .attr('stroke', d => {{
      if (d.type === 'FK') return '#2563eb80';
      if (d.type === 'FK_REF') return '#ea580c80';
      return '#7c3aed66';
    }})
    .attr('stroke-dasharray', d => d.is_inferred ? '5,5' : '0')
    .attr('stroke-width', d => d.type === 'FK_REF' ? 2 : 1.5)
    .attr('marker-end', d => 'url(#arrow-' + d.type + ')')
    .merge(linkSel);

  nodeSel = nodeSel.data(visNodes, d => d.id);
  nodeSel.exit().remove();
  const enter = nodeSel.enter().append('g').style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (e, d) => {{
        if (!isFrozen && !e.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      }})
      .on('drag',  (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
      .on('end',   (e, d) => {{
        if (!isFrozen) {{
          if (!e.active) sim.alphaTarget(0);
          d.fx = null; d.fy = null;
        }}
      }})
    )
    .on('click', (e, d) => showNodeInfo(d));

  enter.append('path')
    .attr('class', 'node-shape')
    .attr('stroke-width', 1.5);
  enter.append('text').attr('class', 'lbl')
    .attr('text-anchor', 'middle').attr('dy', 34)
    .attr('font-size', '9px').attr('font-family', 'IBM Plex Mono,monospace')
    .attr('fill', '#8b949e');
  enter.append('text').attr('class', 'ico')
    .attr('text-anchor', 'middle').attr('dy', 6)
    .attr('font-size', '11px')
    .attr('fill', d => NODE_COLORS[d.type] || NODE_COLORS[d.kind] || '#58a6ff');

  nodeSel = enter.merge(nodeSel);

  const symbol = d3.symbol().size(700);
  nodeSel.select('.node-shape')
    .attr('d', d => {{
      if (d.kind === 'COLUMN') return symbol.type(d3.symbolDiamond)();
      return symbol.type(d3.symbolCircle)();
    }})
    .attr('fill', d => {{
      if (d.kind === 'COLUMN') return 'rgba(124,58,237,0.15)';
      if (d.type === 'REF_TABLE') return 'rgba(234,88,12,0.15)';
      return 'rgba(37,99,235,0.15)';
    }})
    .attr('stroke', d => NODE_COLORS[d.type] || NODE_COLORS[d.kind] || '#2563eb');

  nodeSel.select('.lbl').text(d => d.kind === 'COLUMN' ? d.column : d.id);
  nodeSel.select('.ico').text(d => {{
    if (d.kind === 'COLUMN') return '◇';
    if (d.type === 'REF_TABLE') return '◈';
    return '●';
  }});

  sim.on('tick', () => {{
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
  }});

  sim.nodes(nodes);
  sim.force('link').links(visLinks);
  sim.alpha(0.3).restart();
}}

renderGraph();

function toggleEdge(type, el) {{
  if (type === 'FK') showFK = !showFK;
  el.classList.toggle('active');
  renderGraph();
}}

function toggleColumns(el) {{
  showColumns = !showColumns;
  el.classList.toggle('active');
  el.innerHTML = `<div class="legend-dot" style="background:#e1c4ff"></div> Columns: ${{showColumns ? 'ON' : 'OFF'}}`;
  renderGraph();
}}

function filterGraph() {{
  const val = document.getElementById('graph-filter').value;
  if (!val) {{ buildOverviewGraphData(); }} else {{ buildDetailGraphData(val); }}
  renderGraph();
}}

function resetGraph() {{
  document.getElementById('graph-filter').value = '';
  if (isFrozen) toggleFreeze();
  buildOverviewGraphData();
  renderGraph();
}}

function toggleFreeze() {{
  isFrozen = !isFrozen;
  const btn = document.getElementById('freeze-btn');
  if (isFrozen) {{
    sim.stop();
    nodes.forEach(d => {{ d.fx = d.x; d.fy = d.y; }});
    btn.textContent = '🔥 Unfreeze';
    btn.classList.add('btn-primary');
  }} else {{
    nodes.forEach(d => {{ d.fx = null; d.fy = null; }});
    btn.textContent = '🧊 Freeze';
    btn.classList.remove('btn-primary');
    sim.alpha(0.3).restart();
  }}
}}

function fitGraph() {{
  if (!nodes.length) return;
  const pad = 40;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  nodes.forEach(d => {{
    if (d.x < x0) x0 = d.x;
    if (d.y < y0) y0 = d.y;
    if (d.x > x1) x1 = d.x;
    if (d.y > y1) y1 = d.y;
  }});
  const bw = x1 - x0 || 1;
  const bh = y1 - y0 || 1;
  const scale = Math.min((GW - pad * 2) / bw, (GH - pad * 2) / bh, 2);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const tx = GW / 2 - cx * scale;
  const ty = GH / 2 - cy * scale;
  const transform = d3.zoomIdentity.translate(tx, ty).scale(scale);
  svgEl.transition().duration(500).call(
    d3.zoom().scaleExtent([0.3, 3]).on('zoom', e => gRoot.attr('transform', e.transform)).transform,
    transform
  );
}}

function showNodeInfo(d) {{
  if (d.kind === 'COLUMN') {{
    const tbl = TABLES.find(t => t.table_name === d.table) || {{}};
    const col = (tbl.columns || []).find(c => c.column_name === d.column) || {{}};
    const isFk = !!col.is_foreign_key;
    const isPk = !!col.is_primary_key;
    const bc = isFk ? 'rgba(210,168,255,0.15)' : 'rgba(88,166,255,0.12)';
    const bt = isFk ? NODE_COLORS.COLUMN : NODE_COLORS.TABLE;

    document.getElementById('info-panel').innerHTML = `
      <div class="info-panel-title">Column Info</div>
      <div class="info-node-name">${{d.column}}</div>
      <span class="info-badge" style="background:${{bc}};color:${{bt}};border:1px solid ${{bt}}40">
        COLUMN ${{isPk ? '· PK ' : ''}}${{isFk ? '· FK' : ''}}
      </span>
      <div class="info-stat"><span class="info-stat-label">Table</span><span class="info-stat-val">${{d.table}}</span></div>
      ${{col.data_type ? `<div class="info-stat"><span class="info-stat-label">Type</span><span class="info-stat-val">${{col.data_type}}</span></div>` : ''}}
      <div class="info-stat"><span class="info-stat-label">Nullable</span><span class="info-stat-val">${{col.is_nullable || 'N/A'}}</span></div>
      ${{col.distinct_count != null ? `<div class="info-stat"><span class="info-stat-label">Distinct</span><span class="info-stat-val">${{col.distinct_count.toLocaleString()}}</span></div>` : ''}}
      ${{col.fk_ref_table ? `<div class="info-stat"><span class="info-stat-label">References</span><span class="info-stat-val" style="color:#ffa657">${{col.fk_ref_table}}.${{col.fk_ref_column || 'PK'}}</span></div>` : ''}}
      ${{col.description ? `<div class="info-desc">${{col.description}}</div>` : ''}}
    `;
    return;
  }}

  const tbl = TABLES.find(t => t.table_name === d.id);
  if (!tbl) return;
  // FIX 3: exclude self-loops from displayed relationships
  const relOut = RELATIONSHIPS.filter(r => r.source === d.id && r.target !== r.source).map(r => r.target);
  const relIn  = RELATIONSHIPS.filter(r => r.target === d.id && r.source !== r.target).map(r => r.source);
  const isRef = d.type === 'REF_TABLE';
  const bc = isRef ? 'rgba(255,166,87,0.15)' : 'rgba(88,166,255,0.15)';
  const bt = isRef ? '#ffa657' : '#58a6ff';
  const desc = tbl.description
    ? tbl.description.substring(0, 200) + (tbl.description.length > 200 ? '…' : '')
    : '';

  document.getElementById('info-panel').innerHTML = `
    <div class="info-panel-title">Table Info</div>
    <div class="info-node-name">${{d.id}}</div>
    <span class="info-badge" style="background:${{bc}};color:${{bt}};border:1px solid ${{bt}}40">${{d.type}}</span>
    <div>
      ${{tbl.row_count != null ? `<div class="info-stat"><span class="info-stat-label">Rows</span><span class="info-stat-val">${{tbl.row_count.toLocaleString()}}</span></div>` : ''}}
      ${{tbl.size ? `<div class="info-stat"><span class="info-stat-label">Size</span><span class="info-stat-val">${{tbl.size}}</span></div>` : ''}}
      <div class="info-stat"><span class="info-stat-label">Columns</span><span class="info-stat-val">${{tbl.columns.length}}</span></div>
    </div>
    ${{desc ? `<div class="info-desc">${{desc}}</div>` : ''}}
    ${{(() => {{
      const fkCols = tbl.columns.filter(c => c.is_foreign_key);
      return fkCols.length
        ? `<div><div class="info-panel-title" style="margin-top:4px">FK Columns (${{fkCols.length}})</div>
           <div class="related-table-list">${{fkCols.map(c => `<div class="related-table-chip">${{c.column_name}}</div>`).join('')}}</div></div>`
        : '';
    }})()}}
    ${{relOut.length ? `<div><div class="info-panel-title" style="margin-top:4px">Outbound Foreign Key →</div><div class="related-table-list">${{relOut.map(t => `<div class="related-table-chip" onclick="jumpToTable('${{t}}')">${{t}}</div>`).join('')}}</div></div>` : ''}}
    ${{relIn.length  ? `<div><div class="info-panel-title" style="margin-top:4px">Inbound Foreign Key ←</div><div class="related-table-list">${{relIn.map(t => `<div class="related-table-chip" onclick="jumpToTable('${{t}}')">${{t}}</div>`).join('')}}</div></div>` : ''}}
    <button class="btn" style="margin-top:8px;width:100%" onclick="jumpToTable('${{d.id}}')">⤵ View Table Details</button>
  `;
}}

function jumpToTable(name) {{
  const card = document.querySelector(`[data-table="${{name}}"]`);
  if (card) {{ card.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); card.classList.add('expanded'); }}
}}

// ── Table Cards ───────────────────────────────────────────────────────────
let activeTypeFilter = 'ALL';

function renderTableCards(search = '') {{
  const tc = document.getElementById('table-cards');
  tc.innerHTML = '';
  const q = search.toLowerCase();
  const filtered = TABLES.filter(t => {{
    const matchType   = activeTypeFilter === 'ALL' || t.table_type === activeTypeFilter;
    const matchSearch = !q
      || t.table_name.toLowerCase().includes(q)
      || (t.description || '').toLowerCase().includes(q)
      || t.columns.some(c =>
           c.column_name.toLowerCase().includes(q) ||
           (c.description || '').toLowerCase().includes(q)
         );
    return matchType && matchSearch;
  }});

  filtered.forEach(t => {{
    const card = document.createElement('div');
    card.className = 'table-card';
    card.setAttribute('data-table', t.table_name);
    card.innerHTML = `
      <div class="table-card-header" onclick="this.parentElement.classList.toggle('expanded')">
        <span class="table-type-badge type-base">TABLE</span>
        <span class="table-name">${{t.table_name}}</span>
        <div class="table-meta">
          ${{t.row_count != null ? `<div class="table-meta-item">rows: <span>${{t.row_count.toLocaleString()}}</span></div>` : ''}}
          ${{t.size ? `<div class="table-meta-item">size: <span>${{t.size}}</span></div>` : ''}}
          <div class="table-meta-item">cols: <span>${{t.columns.length}}</span></div>
        </div>
        <div class="expand-icon">▾</div>
      </div>
      <div class="table-card-body">
        ${{t.description ? `<div class="table-desc"><strong>AI Description:</strong><br>${{t.description}}</div>` : ''}}
        <table class="columns-table">
          <thead><tr>
            <th>Column</th><th>Type</th><th>Keys</th><th>Nullable</th><th>Distinct</th><th>AI Description</th>
          </tr></thead>
          <tbody>
            ${{t.columns.map(c => `<tr>
              <td class="col-name">${{c.column_name}}</td>
              <td class="col-type">${{c.data_type}}</td>
              <td style="white-space:nowrap">
                ${{c.is_primary_key ? '<span class="col-pk">PK</span>' : ''}}
                ${{c.inferred_pk ? '<span class="col-pk" style="background:rgba(88,166,255,0.15); color:var(--accent); border:1px dashed var(--accent)">PK (AI)</span>' : ''}}
                ${{c.is_foreign_key ? '<span class="col-fk">FK</span>' : ''}}
                ${{c.inferred_fk ? '<span class="col-fk" style="background:rgba(210,168,255,0.15); border:1px dashed var(--accent3)">FK (AI)</span>' : ''}}
                ${{(c.fk_ref_table || c.inf_ref_table) ? `
                  <div style="font-size:10px; color:${{c.inf_ref_table ? 'var(--accent3)' : '#ffa657'}}; font-family:IBM Plex Mono,monospace; margin-top:2px;">
                    → ${{c.fk_ref_table || c.inf_ref_table}}.${{c.fk_ref_column || c.inf_ref_column || 'PK'}}
                    ${{c.inf_ref_table ? ' <span style="font-style:italic; opacity:0.7">(AI)</span>' : ''}}
                  </div>` : ''}}
              </td>
              <td class="col-nullable">${{c.is_nullable || ''}}</td>
              <td class="col-distinct">${{c.distinct_count != null ? c.distinct_count.toLocaleString() : ''}}</td>
              <td class="col-desc">${{c.description || ''}}</td>
            </tr>`).join('')}}
          </tbody>
        </table>
      </div>`;
    tc.appendChild(card);
  }});
}}

function filterTables() {{ renderTableCards(document.getElementById('search-input').value); }}

function setTypeFilter(type, el) {{
  activeTypeFilter = type;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  filterTables();
}}

// ── Export ────────────────────────────────────────────────────────────────
function exportJSON() {{
  const payload = {{ source: SOURCE, schemas: [{{ schema_name: SOURCE.schema || 'public', tables: TABLES }}] }};
  const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'schema_export.json';
  a.click();
}}

// ── Resize ────────────────────────────────────────────────────────────────
window.addEventListener('resize', () => {{
  GW = document.querySelector('.graph-body').clientWidth - 240;
  svgEl.attr('width', GW);
  sim.force('center', d3.forceCenter(GW / 2, GH / 2)).alpha(0.2).restart();
}});

// ── Init ──────────────────────────────────────────────────────────────────
renderTiles();
renderTableCards();
</script>
</body>
</html>"""


# ── Main Generator ─────────────────────────────────────────────────────────────

def generate_report(data: dict, title: str = None) -> str:
    """Generate and return HTML report string from a schema data dict (no file I/O)."""
    source  = data.get("source", {})
    schemas = data.get("schemas", [])

    all_tables = []
    for schema in schemas:
        all_tables.extend(schema.get("tables", []))

    all_tables    = normalize_tables(all_tables)
    relationships = infer_relationships(all_tables)
    stats         = compute_stats(all_tables, relationships)

    if not title:
        title = f"{source.get('database', 'Database')} Schema Report"

    schema_name  = schemas[0].get("schema_name", source.get("schema", "public")) if schemas else "public"
    db_type      = source.get("db_type", "database")
    database     = source.get("database", "unknown")
    extracted_at = source.get("extracted_at", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    extracted_fmt = extracted_at
    try:
        extracted_fmt = datetime.fromisoformat(
            extracted_at.replace("Z", "+00:00")
        ).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass

    return build_html(
        source, all_tables, relationships, stats, title,
        db_type, database, schema_name, extracted_fmt,
    )


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML schema intelligence report from a JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url",  "-u", default=None, help="FastAPI endpoint URL to fetch schema JSON from.")
    parser.add_argument("--credentials", "-c", default=None,
        help='JSON string of DB credentials e.g. \'{"host":"localhost","port":5432,"user":"u","password":"p","database":"db","db_type":"postgres"}\'')
    parser.add_argument("--output", "-o", default=None, help="Path for the output HTML file.")
    parser.add_argument("--title",  "-t", default=None, help="Custom title for the report.")
    args   = parser.parse_args()
    creds  = json.loads(args.credentials) if args.credentials else {}
    generate_report(args.input, args.output, args.title, args.url, creds)


if __name__ == "__main__":
    generate_report(
        input_path=r"C:\Users\naveenkumar.n\vs code\html_report_json\metadata_readable_1772129480547.json",
        output_path="report.html",
        title="My Database Report"
    )
