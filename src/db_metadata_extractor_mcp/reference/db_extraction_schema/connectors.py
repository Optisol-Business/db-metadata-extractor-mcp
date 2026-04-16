import psycopg2
from psycopg2.extras import RealDictCursor
import snowflake.connector
from snowflake.connector import DictCursor
try:
    import pyodbc
except ImportError:
    pyodbc = None
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
try:
    from google.cloud import bigquery
except ImportError:
    bigquery = None
try:
    import oracledb
except ImportError:
    oracledb = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_ist_now() -> str:
    """Returns current UTC time in ISO format."""
    now_utc = datetime.now(timezone.utc)
    return now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')


def format_size(size_bytes: Optional[int]) -> str:
    """Formats bytes into human-readable strings."""
    if size_bytes is None:
        return "0B"
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            formatted = f"{size:.2f}".rstrip('0').rstrip('.')
            return f"{formatted}{unit}"
        size /= 1024.0
    formatted = f"{size:.2f}".rstrip('0').rstrip('.')
    return f"{formatted}PB"


def get_postgres_connection(creds: Dict[str, Any]):
    host     = creds.get('host', 'localhost').strip()
    port     = creds.get('port', 5432)
    user     = creds.get('user')
    password = creds.get('password')
    database = creds.get('database')
    sslmode  = creds.get('sslmode', 'prefer').lower()
    conn_params = {
        'user': user, 'password': password,
        'host': host, 'port': port,
        'database': database,
        'connect_timeout': 10,
        'sslmode': sslmode,
    }
    return psycopg2.connect(**conn_params)


def get_snowflake_connection(creds: Dict[str, Any]):
    return snowflake.connector.connect(
        user=creds.get('user'),
        password=creds.get('password'),
        account=creds.get('account'),
        warehouse=creds.get('warehouse'),
        database=creds.get('database'),
        schema=creds.get('schema'),
        role=creds.get('role'),
    )


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def fetch_postgres_metadata(creds: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch full metadata from PostgreSQL including:
      - Table names, types, estimated row counts, disk sizes
      - Column names, data types, nullability, generated flag
      - Primary key flags
      - Foreign key flags + explicit references dict
      - Approximate distinct counts via statistics
    """
    conn = get_postgres_connection(creds)
    try:
        target_schema = creds.get('schema', 'public')
        tables_filter = creds.get('tables')
        database      = creds.get('database')

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ── 1. Tables ─────────────────────────────────────────────────
            table_filter_sql = ""
            if tables_filter:
                quoted = ", ".join(f"'{t}'" for t in tables_filter)
                table_filter_sql = f"AND t.relname IN ({quoted})"

            cur.execute(f"""
                SELECT
                    t.relname                                        AS table_name,
                    CASE t.relkind
                        WHEN 'r' THEN 'BASE TABLE'
                        WHEN 'v' THEN 'VIEW'
                        WHEN 'm' THEN 'VIEW'
                        ELSE 'OTHER'
                    END                                              AS table_type,
                    GREATEST(t.reltuples::bigint, 0)                AS row_count,
                    pg_size_pretty(pg_total_relation_size(t.oid))   AS size
                FROM pg_class t
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = %s
                  AND t.relkind = 'r'
                  AND t.relpersistence != 't'
                  {table_filter_sql}
                ORDER BY t.relname
            """, (target_schema,))
            tables_data = cur.fetchall()

            # ── 2. Columns ────────────────────────────────────────────────
            cur.execute(f"""
                SELECT
                    c.table_name,
                    c.column_name,
                    c.udt_name                          AS data_type,
                    c.is_nullable = 'YES'               AS nullable,
                    c.is_generated <> 'NEVER'           AS is_generated,
                    c.ordinal_position
                FROM information_schema.columns c
                WHERE c.table_schema = %s
                  {f"AND c.table_name IN ({', '.join(f'%s' for _ in tables_filter)})" if tables_filter else ""}
                ORDER BY c.table_name, c.ordinal_position
            """, (target_schema, *((tables_filter or []))))
            columns_data = cur.fetchall()

            # ── 3. Primary keys ───────────────────────────────────────────
            cur.execute(f"""
                SELECT
                    kcu.table_name,
                    kcu.column_name
                FROM information_schema.table_constraints   tc
                JOIN information_schema.key_column_usage    kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema    = tc.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema    = %s
            """, (target_schema,))
            pk_set = {(r['table_name'], r['column_name']) for r in cur.fetchall()}

            # ── 3.5. Unique Constraints ───────────────────────────────────
            cur.execute(f"""
                SELECT
                    kcu.table_name,
                    kcu.column_name
                FROM information_schema.table_constraints   tc
                JOIN information_schema.key_column_usage    kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema    = tc.table_schema
                WHERE tc.constraint_type = 'UNIQUE'
                  AND tc.table_schema    = %s
            """, (target_schema,))
            unique_set = {(r['table_name'], r['column_name']) for r in cur.fetchall()}

            # ── 4. Foreign keys (with explicit reference target) ──────────
            cur.execute(f"""
                SELECT
                    kcu.table_name                  AS source_table,
                    kcu.column_name                 AS source_column,
                    ccu.table_schema                AS ref_schema,
                    ccu.table_name                  AS ref_table,
                    ccu.column_name                 AS ref_column
                FROM information_schema.table_constraints   tc
                JOIN information_schema.key_column_usage    kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema    = tc.table_schema
                JOIN information_schema.referential_constraints rc
                  ON rc.constraint_name  = tc.constraint_name
                 AND rc.constraint_schema = tc.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name  = rc.unique_constraint_name
                 AND ccu.constraint_schema = rc.unique_constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema    = %s
            """, (target_schema,))
            fk_map: Dict[tuple, dict] = {}
            for r in cur.fetchall():
                fk_map[(r['source_table'], r['source_column'])] = {
                    "schema": r['ref_schema'],
                    "table":  r['ref_table'],
                    "column": r['ref_column'],
                }

            # ── 5. Distinct counts from pg_stats ──────────────────────────
            cur.execute(f"""
                SELECT
                    tablename,
                    attname     AS column_name,
                    n_distinct
                FROM pg_stats
                WHERE schemaname = %s
            """, (target_schema,))
            distinct_map: Dict[tuple, Any] = {}
            for r in cur.fetchall():
                distinct_map[(r['tablename'], r['column_name'])] = r['n_distinct']

        # ── Assemble ──────────────────────────────────────────────────────
        tables_dict: Dict[str, dict] = {}
        for tbl in tables_data:
            tables_dict[tbl['table_name']] = {
                "table_name": tbl['table_name'],
                "table_type": tbl['table_type'],
                "row_count":  int(tbl['row_count']) if tbl['row_count'] is not None else None,
                "size":       tbl['size'],
                "columns":    [],
            }

        for col in columns_data:
            t_name  = col['table_name']
            c_name  = col['column_name']
            is_pk   = (t_name, c_name) in pk_set
            is_unique = (t_name, c_name) in unique_set
            fk_ref  = fk_map.get((t_name, c_name))
            nd      = distinct_map.get((t_name, c_name))
            # pg_stats: negative means fraction of total rows → convert to approx count
            row_count = (tables_dict.get(t_name) or {}).get('row_count') or 0
            if isinstance(nd, float) and nd < 0:
                nd = int(abs(nd) * row_count)

            col_entry: dict = {
                "column_name":  c_name,
                "data_type":    col['data_type'].upper(),
                "nullable":     bool(col['nullable']),
                "unique":       is_unique,
                "is_generated": bool(col['is_generated']),
                "primary_key":  is_pk,
                "foreign_key":  fk_ref is not None,
                "distinct_count": int(nd) if nd is not None else None,
            }
            if fk_ref:
                col_entry["references"] = fk_ref

            if t_name in tables_dict:
                tables_dict[t_name]["columns"].append(col_entry)

        return {
            "source": {
                "db_type":      "postgres",
                "database":     database,
                "schema":       target_schema,
                "extracted_at": get_ist_now(),
            },
            "schemas": [{"schema_name": target_schema, "tables": list(tables_dict.values())}],
        }
    finally:
        conn.close()


# ── BigQuery ──────────────────────────────────────────────────────────────────

def fetch_bigquery_metadata(creds: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch metadata from BigQuery including:
      - Table names, types, row counts
      - Column names, data types, nullability
      - Primary key flags  (from INFORMATION_SCHEMA.TABLE_CONSTRAINTS, BQ standard edition+)
      - Foreign key flags + references  (from INFORMATION_SCHEMA.KEY_COLUMN_USAGE)
      - Approximate distinct counts  (from INFORMATION_SCHEMA.COLUMN_FIELD_PATHS where available)

    Falls back gracefully when constraint metadata is unavailable (e.g. legacy projects).
    """
    if not bigquery:
        raise ImportError("google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery")

    project_id     = creds.get('project_id')
    key_path       = creds.get('service_account_key')
    dataset_id     = creds.get('schema_name') or creds.get('schema')
    tables_filter  = creds.get('tables')

    client = (
        bigquery.Client.from_service_account_json(key_path)
        if key_path
        else bigquery.Client(project=project_id)
    )

    # ── 1. Tables + columns via list_tables + get_table ──────────────────
    bq_tables = list(client.list_tables(f"{project_id}.{dataset_id}"))

    # ── 2. Primary keys (BQ standard edition, may fail on legacy) ─────────
    pk_set: set = set()
    unique_set: set = set()
    try:
        pk_query = f"""
            SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME
            FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` tc
            JOIN `{project_id}.{dataset_id}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE` kcu
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        """
        for row in client.query(pk_query).result():
            pk_set.add((row.TABLE_NAME, row.COLUMN_NAME))
            
        uq_query = f"""
            SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME
            FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` tc
            JOIN `{project_id}.{dataset_id}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE` kcu
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'UNIQUE'
        """
        for row in client.query(uq_query).result():
            unique_set.add((row.TABLE_NAME, row.COLUMN_NAME))
    except Exception:
        pass  # constraints not available on this project/edition

    # ── 3. Foreign keys ────────────────────────────────────────────────────
    fk_map: Dict[tuple, dict] = {}
    try:
        fk_query = f"""
            SELECT
                kcu.TABLE_NAME          AS source_table,
                kcu.COLUMN_NAME         AS source_column,
                ccu.TABLE_SCHEMA        AS ref_schema,
                ccu.TABLE_NAME          AS ref_table,
                ccu.COLUMN_NAME         AS ref_column
            FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` tc
            JOIN `{project_id}.{dataset_id}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE` kcu
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            JOIN `{project_id}.{dataset_id}.INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE` ccu
              ON ccu.CONSTRAINT_NAME = tc.UNIQUE_CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
        """
        for row in client.query(fk_query).result():
            fk_map[(row.source_table, row.source_column)] = {
                "schema": row.ref_schema,
                "table":  row.ref_table,
                "column": row.ref_column,
            }
    except Exception:
        pass

    # ── 4. Assemble tables ─────────────────────────────────────────────────
    tables_list = []
    for bq_table in bq_tables:
        if tables_filter and bq_table.table_id not in tables_filter:
            continue

        # Only extract permanent base tables
        if bq_table.table_type != 'TABLE':
            continue

        schema_obj = client.get_table(bq_table)
        columns    = []

        # Get distinct counts for all columns in one query
        distinct_map: Dict[str, int] = {}
        if schema_obj.schema and schema_obj.num_rows and schema_obj.num_rows > 0:
            try:
                agg_parts = ", ".join(
                    f"APPROX_COUNT_DISTINCT(`{field.name}`) AS `dc_{i}`"
                    for i, field in enumerate(schema_obj.schema)
                )
                dc_query = f"SELECT {agg_parts} FROM `{project_id}.{dataset_id}.{bq_table.table_id}`"
                dc_result = list(client.query(dc_query).result())
                if dc_result:
                    row = dc_result[0]
                    for i, field in enumerate(schema_obj.schema):
                        val = getattr(row, f'dc_{i}', None)
                        if val is not None:
                            distinct_map[field.name] = int(val)
            except Exception:
                pass

        for field in schema_obj.schema:
            c_name = field.name
            t_name = bq_table.table_id
            is_pk  = (t_name, c_name) in pk_set
            is_unique = (t_name, c_name) in unique_set
            fk_ref = fk_map.get((t_name, c_name))

            col_entry: dict = {
                "column_name":  c_name,
                "data_type":    field.field_type,
                "nullable":     field.is_nullable,
                "unique":       is_unique,
                "is_generated": False,
                "primary_key":  is_pk,
                "foreign_key":  fk_ref is not None,
                "distinct_count": distinct_map.get(c_name),
            }
            if fk_ref:
                col_entry["references"] = fk_ref

            columns.append(col_entry)

        tables_list.append({
            "table_name": bq_table.table_id,
            "table_type": "BASE TABLE",
            "row_count":  schema_obj.num_rows,
            "size":       format_size(schema_obj.num_bytes),
            "columns":    columns,
        })

    return {
        "source": {
            "db_type":      "bigquery",
            "database":     project_id,
            "schema":       dataset_id,
            "extracted_at": get_ist_now(),
        },
        "schemas": [{"schema_name": dataset_id, "tables": tables_list}],
    }


# ── Oracle ────────────────────────────────────────────────────────────────────

def fetch_oracle_metadata(creds: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch metadata from Oracle including:
      - Table names, types, row counts (from ALL_TABLES), segment sizes
      - Column names, data types, nullability, virtual (generated) flag
      - Primary key flags
      - Foreign key flags + explicit references dict
      - Distinct counts from ALL_TAB_COL_STATISTICS (num_distinct, when stats are gathered)
    """
    if not oracledb:
        raise ImportError("oracledb not installed. Run: pip install oracledb")

    user          = creds.get('user') or creds.get('username')
    password      = creds.get('password')
    host          = creds.get('host')
    port          = creds.get('port', 1521)
    service_name  = creds.get('database_name') or creds.get('database')
    
    # Construct DSN: prioritizing explicit DSN string, then building EZConnect
    dsn = creds.get('dsn')
    if not dsn and host and service_name:
        dsn = f"{host}:{port}/{service_name}"
    
    if not dsn:
        dsn = host  # Fallback

    schema        = (creds.get('schema_name') or creds.get('schema') or user).upper()
    tables_filter = creds.get('tables')
    database      = service_name or dsn

    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    try:
        cur = conn.cursor()

        table_filter_sql = ""
        if tables_filter:
            quoted = ", ".join(f"'{t.upper()}'" for t in tables_filter)
            table_filter_sql = f"AND TABLE_NAME IN ({quoted})"

        # ── 1. Tables ─────────────────────────────────────────────────────
        cur.execute(f"""
            SELECT
                t.TABLE_NAME,
                t.NUM_ROWS,
                s.BYTES
            FROM ALL_TABLES t
            LEFT JOIN DBA_SEGMENTS s
              ON s.OWNER = t.OWNER AND s.SEGMENT_NAME = t.TABLE_NAME
            WHERE t.OWNER = :schema
              AND t.TEMPORARY = 'N'
            {table_filter_sql}
            ORDER BY t.TABLE_NAME
        """, schema=schema)
        tables_data = cur.fetchall()



        # ── 2. Columns ────────────────────────────────────────────────────
        cur.execute(f"""
            SELECT
                c.TABLE_NAME,
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.NULLABLE,
                c.VIRTUAL_COLUMN
            FROM ALL_TAB_COLS c
            WHERE c.OWNER = :schema
              AND c.HIDDEN_COLUMN = 'NO'
            {table_filter_sql.replace('TABLE_NAME', 'c.TABLE_NAME')}
            ORDER BY c.TABLE_NAME, c.COLUMN_ID
        """, schema=schema)
        columns_data = cur.fetchall()

        # ── 3. Primary keys ────────────────────────────────────────────────
        cur.execute(f"""
            SELECT
                acc.TABLE_NAME,
                acc.COLUMN_NAME
            FROM ALL_CONSTRAINTS  ac
            JOIN ALL_CONS_COLUMNS acc
              ON acc.CONSTRAINT_NAME = ac.CONSTRAINT_NAME
             AND acc.OWNER           = ac.OWNER
            WHERE ac.CONSTRAINT_TYPE = 'P'
              AND ac.OWNER           = :schema
        """, schema=schema)
        pk_set = {(r[0], r[1]) for r in cur.fetchall()}

        # ── 3.5. Unique constraints ────────────────────────────────────────
        cur.execute(f"""
            SELECT
                acc.TABLE_NAME,
                acc.COLUMN_NAME
            FROM ALL_CONSTRAINTS  ac
            JOIN ALL_CONS_COLUMNS acc
              ON acc.CONSTRAINT_NAME = ac.CONSTRAINT_NAME
             AND acc.OWNER           = ac.OWNER
            WHERE ac.CONSTRAINT_TYPE = 'U'
              AND ac.OWNER           = :schema
        """, schema=schema)
        unique_set = {(r[0], r[1]) for r in cur.fetchall()}

        # ── 4. Foreign keys (with explicit references) ─────────────────────
        cur.execute(f"""
            SELECT
                acc_fk.TABLE_NAME   AS source_table,
                acc_fk.COLUMN_NAME  AS source_column,
                ac_pk.OWNER         AS ref_schema,
                acc_pk.TABLE_NAME   AS ref_table,
                acc_pk.COLUMN_NAME  AS ref_column
            FROM ALL_CONSTRAINTS   ac_fk
            JOIN ALL_CONS_COLUMNS  acc_fk
              ON acc_fk.CONSTRAINT_NAME = ac_fk.CONSTRAINT_NAME
             AND acc_fk.OWNER           = ac_fk.OWNER
            JOIN ALL_CONSTRAINTS   ac_pk
              ON ac_pk.CONSTRAINT_NAME  = ac_fk.R_CONSTRAINT_NAME
             AND ac_pk.OWNER            = ac_fk.R_OWNER
            JOIN ALL_CONS_COLUMNS  acc_pk
              ON acc_pk.CONSTRAINT_NAME = ac_pk.CONSTRAINT_NAME
             AND acc_pk.OWNER           = ac_pk.OWNER
             AND acc_pk.POSITION        = acc_fk.POSITION
            WHERE ac_fk.CONSTRAINT_TYPE = 'R'
              AND ac_fk.OWNER           = :schema
        """, schema=schema)
        fk_map: Dict[tuple, dict] = {}
        for r in cur.fetchall():
            fk_map[(r[0], r[1])] = {
                "schema": r[2],
                "table":  r[3],
                "column": r[4],
            }

        # ── 5. Distinct counts from statistics ────────────────────────────
        cur.execute(f"""
            SELECT TABLE_NAME, COLUMN_NAME, NUM_DISTINCT
            FROM ALL_TAB_COL_STATISTICS
            WHERE OWNER = :schema
        """, schema=schema)
        distinct_map = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        # ── Assemble ──────────────────────────────────────────────────────
        tables_dict: Dict[str, dict] = {}
        for (t_name, num_rows, size_bytes) in tables_data:
            tables_dict[t_name] = {
                "table_name": t_name,
                "table_type": "BASE TABLE",
                "row_count":  int(num_rows) if num_rows is not None else None,
                "size":       format_size(size_bytes),
                "columns":    [],
            }

        for (t_name, c_name, data_type, nullable, virtual_col) in columns_data:
            if t_name not in tables_dict:
                continue
            is_pk  = (t_name, c_name) in pk_set
            is_unique = (t_name, c_name) in unique_set
            fk_ref = fk_map.get((t_name, c_name))
            nd     = distinct_map.get((t_name, c_name))

            col_entry: dict = {
                "column_name":   c_name,
                "data_type":     data_type,
                "nullable":      nullable == 'Y',
                "unique":        is_unique,
                "is_generated":  virtual_col == 'YES',
                "primary_key":   is_pk,
                "foreign_key":   fk_ref is not None,
                "distinct_count": int(nd) if nd is not None else None,
            }
            if fk_ref:
                col_entry["references"] = fk_ref

            tables_dict[t_name]["columns"].append(col_entry)

        return {
            "source": {
                "db_type":      "oracle",
                "database":     database,
                "schema":       schema,
                "extracted_at": get_ist_now(),
            },
            "schemas": [{"schema_name": schema, "tables": list(tables_dict.values())}],
        }
    finally:
        conn.close()


# ── Snowflake ─────────────────────────────────────────────────────────────────

def fetch_snowflake_metadata(creds: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_snowflake_connection(creds)
    try:
        cursor        = conn.cursor(DictCursor)
        target_db     = creds.get('database').upper()
        target_schema = creds.get('schema').upper()
        tables_filter = creds.get('tables')
        table_filter_clause = ""

        if tables_filter:
            tables_quoted       = "', '".join([t.upper() for t in tables_filter])
            table_filter_clause = f"AND TABLE_NAME IN ('{tables_quoted}')"

        # 1. Tables
        cursor.execute(f"""
            SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_CATALOG = '{target_db}' AND TABLE_SCHEMA = '{target_schema}'
            AND TABLE_TYPE = 'BASE TABLE'
            {table_filter_clause}
        """)
        tables_data = cursor.fetchall()

        # 2. Columns
        cursor.execute(f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_CATALOG = '{target_db}' AND TABLE_SCHEMA = '{target_schema}'
            {table_filter_clause}
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """)
        columns_data = cursor.fetchall()

        # 3. Primary keys
        pk_set: set = set()
        unique_set: set = set()
        try:
            cursor.execute(f"SHOW PRIMARY KEYS IN SCHEMA \"{target_db}\".\"{target_schema}\"")
            for row in cursor.fetchall():
                pk_set.add((row['table_name'], row['column_name']))
        except Exception:
            pass
            
        try:
            cursor.execute(f"SHOW UNIQUE KEYS IN SCHEMA \"{target_db}\".\"{target_schema}\"")
            for row in cursor.fetchall():
                unique_set.add((row['table_name'], row['column_name']))
        except Exception:
            pass

        # 4. Foreign keys
        fk_map: Dict[tuple, dict] = {}
        try:
            cursor.execute(f"SHOW IMPORTED KEYS IN SCHEMA \"{target_db}\".\"{target_schema}\"")
            for row in cursor.fetchall():
                fk_map[(row['fk_table_name'], row['fk_column_name'])] = {
                    "schema": row.get('pk_schema_name', target_schema),
                    "table":  row['pk_table_name'],
                    "column": row['pk_column_name'],
                }
        except Exception:
            pass

        # 5. Distinct counts via APPROX_COUNT_DISTINCT (per-table batch)
        distinct_map: Dict[tuple, Any] = {}
        for tbl in tables_data:
            t_name = tbl['TABLE_NAME']
            t_cols = [c for c in columns_data if c['TABLE_NAME'] == t_name]
            if not t_cols:
                continue
            try:
                agg_parts = ", ".join(
                    f'APPROX_COUNT_DISTINCT("{c["COLUMN_NAME"]}") AS "dc_{i}"'
                    for i, c in enumerate(t_cols)
                )
                cursor.execute(f'SELECT {agg_parts} FROM "{target_db}"."{target_schema}"."{t_name}"')
                row = cursor.fetchone()
                if row:
                    for i, c in enumerate(t_cols):
                        val = row.get(f'dc_{i}')
                        if val is not None:
                            distinct_map[(t_name, c['COLUMN_NAME'])] = int(val)
            except Exception:
                pass

        tables_dict: Dict[str, dict] = {}
        for tbl in tables_data:
            t_name = tbl['TABLE_NAME']
            tables_dict[t_name] = {
                "table_name": t_name,
                "table_type": tbl['TABLE_TYPE'],
                "row_count":  tbl['ROW_COUNT'],
                "size":       format_size(tbl['BYTES']),
                "columns":    [],
            }

        for col in columns_data:
            t_name = col['TABLE_NAME']
            if t_name in tables_dict:
                c_name = col['COLUMN_NAME']
                is_pk  = (t_name, c_name) in pk_set
                is_unique = (t_name, c_name) in unique_set
                fk_ref = fk_map.get((t_name, c_name))
                nd     = distinct_map.get((t_name, c_name))

                col_entry: dict = {
                    "column_name":   c_name,
                    "data_type":     col['DATA_TYPE'],
                    "nullable":      col['IS_NULLABLE'] == 'YES',
                    "unique":        is_unique,
                    "is_generated":  False,
                    "primary_key":   is_pk,
                    "foreign_key":   fk_ref is not None,
                    "distinct_count": int(nd) if nd is not None else None,
                }
                if fk_ref:
                    col_entry["references"] = fk_ref

                tables_dict[t_name]["columns"].append(col_entry)

        return {
            "source": {
                "db_type":      "snowflake",
                "database":     target_db,
                "schema":       target_schema,
                "extracted_at": get_ist_now(),
            },
            "schemas": [{"schema_name": target_schema, "tables": list(tables_dict.values())}],
        }
    finally:
        conn.close()


# ── SQL Server ────────────────────────────────────────────────────────────────

def fetch_sqlserver_metadata(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch metadata from SQL Server using T-SQL queries via pyodbc."""
    if not pyodbc:
        raise ImportError("pyodbc not installed. Run: pip install pyodbc")

    host          = creds.get('host', 'localhost')
    port          = creds.get('port', 1433) or 1433
    user          = creds.get('user') or creds.get('username')
    password      = creds.get('password')
    database      = creds.get('database')
    target_schema = creds.get('schema', 'dbo')
    tables_filter = creds.get('tables')

    print(f"Connecting to SQL Server: host={host}, port={port}, user={user}, database={database}")

    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={host},{port};DATABASE={database};UID={user};PWD={password}"
    )
    conn = pyodbc.connect(conn_str)
    try:
        cursor = conn.cursor()

        table_filter_clause = ""
        if tables_filter:
            tables_quoted       = "', '".join(tables_filter)
            table_filter_clause = f"AND TABLE_NAME IN ('{tables_quoted}')"

        # 1. Tables
        cursor.execute(f"""
            SELECT t.TABLE_NAME, t.TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES t
            WHERE t.TABLE_SCHEMA = '{target_schema}'
            AND t.TABLE_TYPE = 'BASE TABLE'
            AND t.TABLE_NAME <> 'sysdiagrams'
            AND t.TABLE_NAME NOT LIKE '#%'
            {table_filter_clause.replace('TABLE_NAME', 't.TABLE_NAME')}
        """)
        tables_data = [dict(zip([c[0] for c in cursor.description], r)) for r in cursor.fetchall()]

        # 2. Columns
        cursor.execute(f"""
            SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS c
            WHERE c.TABLE_SCHEMA = '{target_schema}'
            {table_filter_clause.replace('TABLE_NAME', 'c.TABLE_NAME')}
            ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
        """)
        columns_data = [dict(zip([c[0] for c in cursor.description], r)) for r in cursor.fetchall()]

        # 3. Primary keys
        cursor.execute(f"""
            SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
              AND tc.TABLE_SCHEMA = '{target_schema}'
            {table_filter_clause.replace('TABLE_NAME', 'kcu.TABLE_NAME')}
        """)
        pk_set = {(r[0], r[1]) for r in cursor.fetchall()}

        # 3.5. Unique keys
        cursor.execute(f"""
            SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'UNIQUE'
              AND tc.TABLE_SCHEMA = '{target_schema}'
            {table_filter_clause.replace('TABLE_NAME', 'kcu.TABLE_NAME')}
        """)
        unique_set = {(r[0], r[1]) for r in cursor.fetchall()}

        # 4. Foreign keys (with explicit references)
        cursor.execute(f"""
            SELECT
                kcu.TABLE_NAME              AS source_table,
                kcu.COLUMN_NAME             AS source_column,
                ccu.TABLE_SCHEMA            AS ref_schema,
                ccu.TABLE_NAME              AS ref_table,
                ccu.COLUMN_NAME             AS ref_column
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON kcu.CONSTRAINT_NAME  = tc.CONSTRAINT_NAME
             AND kcu.TABLE_SCHEMA     = tc.TABLE_SCHEMA
            JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
              ON rc.CONSTRAINT_NAME   = tc.CONSTRAINT_NAME
             AND rc.CONSTRAINT_SCHEMA = tc.TABLE_SCHEMA
            JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
              ON ccu.CONSTRAINT_NAME  = rc.UNIQUE_CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE  = 'FOREIGN KEY'
              AND tc.TABLE_SCHEMA     = '{target_schema}'
        """)
        fk_map: Dict[tuple, dict] = {}
        for r in cursor.fetchall():
            fk_map[(r[0], r[1])] = {"schema": r[2], "table": r[3], "column": r[4]}

        # 5. Row counts + sizes from sys tables
        cursor.execute(f"""
            SELECT
                t.NAME                          AS TABLE_NAME,
                SUM(p.rows)                     AS ROW_COUNT,
                SUM(a.total_pages) * 8 * 1024   AS SIZE_BYTES
            FROM sys.tables t
            INNER JOIN sys.schemas s        ON s.schema_id  = t.schema_id
            INNER JOIN sys.indexes i        ON t.OBJECT_ID  = i.object_id
            INNER JOIN sys.partitions p     ON i.object_id  = p.OBJECT_ID
                                           AND i.index_id   = p.index_id
            INNER JOIN sys.allocation_units a ON p.partition_id = a.container_id
            WHERE s.name = '{target_schema}'
              AND i.index_id <= 1
            GROUP BY t.NAME
        """)
        row_size_map = {}
        for r in cursor.fetchall():
            row_size_map[r[0]] = {"row_count": r[1], "size_bytes": r[2]}

        # 6. Distinct counts from column statistics
        distinct_map: Dict[tuple, Any] = {}
        try:
            cursor.execute(f"""
                SELECT
                    OBJECT_NAME(s.object_id)  AS TABLE_NAME,
                    c.name                    AS COLUMN_NAME,
                    sp.rows
                FROM sys.stats s
                CROSS APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) sp
                INNER JOIN sys.stats_columns sc ON sc.object_id = s.object_id
                                               AND sc.stats_id  = s.stats_id
                INNER JOIN sys.columns c        ON c.object_id  = sc.object_id
                                               AND c.column_id  = sc.column_id
                INNER JOIN sys.tables t         ON t.object_id  = s.object_id
                INNER JOIN sys.schemas sch      ON sch.schema_id = t.schema_id
                WHERE sch.name = '{target_schema}'
                  AND sc.stats_column_id = 1
            """)
            for r in cursor.fetchall():
                distinct_map[(r[0], r[1])] = int(r[2]) if r[2] is not None else None
        except Exception:
            pass  # stats may not be available

        # Assemble
        tables_dict: Dict[str, dict] = {}
        for tbl in tables_data:
            t_name = tbl['TABLE_NAME']
            rs = row_size_map.get(t_name, {})
            tables_dict[t_name] = {
                "table_name": t_name,
                "table_type": tbl.get('TABLE_TYPE') or 'BASE TABLE',
                "row_count":  rs.get("row_count"),
                "size":       format_size(rs.get("size_bytes")),
                "columns":    [],
            }

        for col in columns_data:
            t_name = col['TABLE_NAME']
            c_name = col['COLUMN_NAME']
            if t_name not in tables_dict:
                continue
            is_pk  = (t_name, c_name) in pk_set
            fk_ref = fk_map.get((t_name, c_name))
            nd     = distinct_map.get((t_name, c_name))

            col_entry: dict = {
                "column_name":   c_name,
                "data_type":     col['DATA_TYPE'].upper(),
                "nullable":      col['IS_NULLABLE'] == 'YES',
                "is_generated":  False,
                "primary_key":   is_pk,
                "foreign_key":   fk_ref is not None,
                "distinct_count": int(nd) if nd is not None else None,
            }
            if fk_ref:
                col_entry["references"] = fk_ref

            tables_dict[t_name]["columns"].append(col_entry)

        return {
            "source": {
                "db_type":      "sqlserver",
                "database":     database,
                "schema":       target_schema,
                "extracted_at": get_ist_now(),
            },
            "schemas": [{"schema_name": target_schema, "tables": list(tables_dict.values())}],
        }
    finally:
        conn.close()


# ── Entry Point ───────────────────────────────────────────────────────────────

def extract_metadata(
    db_type: str,
    connection_params: Dict[str, Any],
    tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    params = {**connection_params, "tables": tables}

    if db_type == "postgres":
        params['database'] = connection_params.get('database_name') or connection_params.get('database')
        params['user']     = connection_params.get('username')      or connection_params.get('user')
        return fetch_postgres_metadata(params)

    elif db_type == "snowflake":
        params['database'] = connection_params.get('database_name') or connection_params.get('database')
        params['user']     = connection_params.get('username')      or connection_params.get('user')
        return fetch_snowflake_metadata(params)

    elif db_type == "sqlserver":
        params['database'] = connection_params.get('database_name') or connection_params.get('database')
        params['user']     = connection_params.get('username')      or connection_params.get('user')
        params['host']     = connection_params.get('host')
        params['port']     = connection_params.get('port', 1433)
        params['schema']   = connection_params.get('schema_name', 'dbo')
        return fetch_sqlserver_metadata(params)

    elif db_type == "bigquery":
        params['schema_name'] = connection_params.get('schema_name') or connection_params.get('schema')
        return fetch_bigquery_metadata(params)

    elif db_type == "oracle":
        params['schema_name'] = connection_params.get('schema_name') or connection_params.get('schema')
        params['user']        = connection_params.get('username')    or connection_params.get('user')
        return fetch_oracle_metadata(params)

    else:
        raise ValueError(f"Unsupported database type: {db_type}")