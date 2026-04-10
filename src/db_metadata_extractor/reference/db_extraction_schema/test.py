import streamlit as st
import requests
import json

API_URL = "http://localhost:8000"

st.set_page_config(page_title="Schema Report Generator", layout="wide")
st.title("🗄️ Schema Report Generator")

# ── Sidebar: DB Credentials ──────────────────────────────────────────────
st.sidebar.header("Database Connection")

with st.sidebar.form("db_form"):
    system_name = st.text_input("System Name *", placeholder="e.g. RSBilling")
    owner_name = st.text_input("Owner Name *", placeholder="e.g. John")
    user_id = st.text_input("User ID *", placeholder="e.g. 1489")

    st.divider()

    db_type = st.selectbox("DB Type", ["sqlserver", "postgres", "oracle", "snowflake", "bigquery"])
    host = st.text_input("Host", "localhost")
    port = st.number_input("Port", value=1433)
    database_name = st.text_input("Database Name")
    schema_name = st.text_input("Schema Name", "dbo")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    # Snowflake-specific fields
    account = st.text_input("Snowflake Account", "")
    warehouse = st.text_input("Snowflake Warehouse", "")

    extract_btn = st.form_submit_button("🔍 Extract Raw Metadata")


# ── Step 1: Extract Raw Metadata ─────────────────────────────────────────
if extract_btn:
    if not system_name or not owner_name or not user_id:
        st.error("System Name, Owner Name, and User ID are required.")
    else:
        with st.spinner("Extracting raw metadata from database..."):
            payload = {
                "db_type": db_type,
                "host": host,
                "port": int(port),
                "database_name": database_name,
                "schema_name": schema_name,
                "username": username,
                "password": password,
                "system_name": system_name,
                "owner_name": owner_name,
                "user_id": user_id,
            }
            if account:
                payload["account"] = account
            if warehouse:
                payload["warehouse"] = warehouse

            response = requests.post(f"{API_URL}/api/metadata/extract", json=payload)

        if response.status_code == 200:
            result = response.json()
            st.session_state["system_name"] = system_name
            st.session_state["metadata"] = result.get("data")
            st.success(f"✅ Raw metadata extracted for '{system_name}' and saved to file.")
            st.json(result.get("data"))
        else:
            st.error(f"Error: {response.text}")


# ── Step 2 & 3: Enrich + Report ──────────────────────────────────────────
st.divider()

col1, col2, col3 = st.columns(3)

# System name input for enrich/report (pre-filled from extract)
sys_name_input = st.text_input(
    "System Name (for Enrich / Report)",
    value=st.session_state.get("system_name", ""),
    placeholder="Enter system name",
)

col_a, col_b = st.columns(2)

with col_a:
    enrich_btn = st.button("🤖 Generate AI Descriptions", use_container_width=True)

with col_b:
    report_btn = st.button("📊 Generate HTML Report", use_container_width=True)


# ── Step 2: Enrich with AI ───────────────────────────────────────────────
if enrich_btn:
    if not sys_name_input:
        st.error("Enter a system name to enrich.")
    else:
        with st.spinner("Generating AI descriptions... (this may take a while)"):
            response = requests.post(
                f"{API_URL}/api/metadata/enrich",
                json={"system_name": sys_name_input},
            )

        if response.status_code == 200:
            result = response.json()
            st.session_state["metadata"] = result.get("data")
            st.success(f"✅ AI descriptions generated for '{sys_name_input}' and saved in-place.")
            st.json(result.get("data"))
        else:
            st.error(f"Error: {response.text}")


# ── Step 3: Generate HTML Report ─────────────────────────────────────────
if report_btn:
    if not sys_name_input:
        st.error("Enter a system name to generate the report.")
    else:
        with st.spinner("Generating HTML report..."):
            response = requests.post(
                f"{API_URL}/api/metadata/report",
                json={"system_name": sys_name_input},
            )

        if response.status_code == 200:
            st.success(f"✅ Report generated for '{sys_name_input}'!")
            st.download_button(
                label="📥 Download HTML Report",
                data=response.content,
                file_name=f"{sys_name_input}_schema_report.html",
                mime="text/html",
                use_container_width=True,
            )
        else:
            st.error(f"Error: {response.text}")