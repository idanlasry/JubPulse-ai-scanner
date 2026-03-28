from fastmcp import FastMCP
from supabase import create_client, Client
from dotenv import load_dotenv
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-supabase")

# Load credentials
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Safety guard — only these tables are accessible
ALLOWED_TABLES = ["jobs"]

# Init Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

mcp = FastMCP("supabase-mcp")


def _check_table(table: str) -> str | None:
    """Returns error string if table not allowed, None if OK."""
    if table not in ALLOWED_TABLES:
        return f"Error: table '{table}' is not in the allowed list: {ALLOWED_TABLES}"
    return None


@mcp.tool()
def list_tables() -> list[str]:
    """Returns the list of tables accessible via this MCP server."""
    logger.info("list_tables called")
    return ALLOWED_TABLES


@mcp.tool()
def describe_table(table: str) -> dict:
    """Returns column names and types for a given table by fetching one row."""
    logger.info(f"describe_table called: {table}")
    err = _check_table(table)
    if err:
        return {"error": err}
    try:
        response = supabase.table(table).select("*").limit(1).execute()
        if not response.data:
            return {"error": "Table is empty — cannot infer schema"}
        columns = {col: type(val).__name__ for col, val in response.data[0].items()}
        logger.info(f"describe_table success: {len(columns)} columns")
        return columns
    except Exception as e:
        logger.error(f"describe_table failed: {e}")
        return {"error": str(e)}


@mcp.tool()
def select_query(sql: str) -> list[dict]:
    """
    Run a raw read-only SQL SELECT query against Supabase.
    Supports aggregations, GROUP BY, WHERE, ORDER BY, LIMIT — any valid SELECT.
    Example: SELECT source_group, AVG(confidence_score) FROM jobs GROUP BY source_group
    """
    logger.info(f"select_query called: {sql}")
    if not sql.strip().upper().startswith("SELECT"):
        return [{"error": "Only SELECT queries are allowed"}]
    try:
        response = supabase.rpc("run_select", {"query": sql}).execute()
        logger.info(f"select_query returned {len(response.data)} rows")
        return response.data
    except Exception as e:
        logger.error(f"select_query failed: {e}")
        return [{"error": str(e)}]


@mcp.tool()
def get_recent_rows(
    table: str, limit: int = 10, filter_column: str = None, filter_value: str = None
) -> list[dict]:
    """
    Returns the most recent N rows from a table ordered by timestamp descending.
    Optionally filter by a single column value.
    Example: get_recent_rows("jobs", limit=5, filter_column="alerted", filter_value="false")
    """
    logger.info(f"get_recent_rows called: {table}, limit={limit}")
    err = _check_table(table)
    if err:
        return [{"error": err}]
    try:
        query = (
            supabase.table(table).select("*").order("timestamp", desc=True).limit(limit)
        )
        if filter_column and filter_value is not None:
            query = query.eq(filter_column, filter_value)
        response = query.execute()
        logger.info(f"get_recent_rows returned {len(response.data)} rows")
        return response.data
    except Exception as e:
        logger.error(f"get_recent_rows failed: {e}")
        return [{"error": str(e)}]


@mcp.tool()
def dry_run_update(
    table: str, filter_column: str, filter_value: str, updates: dict
) -> dict:
    """
    Preview which rows WOULD be affected by an update — without making any changes.
    Returns the matching rows and count so you can confirm before running update_query.
    Example: dry_run_update("jobs", "alerted", "false", {"alerted": True})
    """
    logger.info(
        f"dry_run_update called: {table}, {filter_column}={filter_value}, updates={updates}"
    )
    err = _check_table(table)
    if err:
        return {"error": err}
    try:
        response = (
            supabase.table(table).select("*").eq(filter_column, filter_value).execute()
        )
        logger.info(f"dry_run_update: {len(response.data)} rows would be affected")
        return {
            "rows_affected": len(response.data),
            "updates_to_apply": updates,
            "preview": response.data,
        }
    except Exception as e:
        logger.error(f"dry_run_update failed: {e}")
        return {"error": str(e)}


@mcp.tool()
def update_query(
    table: str, filter_column: str, filter_value: str, updates: dict
) -> dict:
    """
    Update rows in a table where filter_column = filter_value, applying the given updates.
    Returns status and number of rows affected.
    Example: update_query("jobs", "alerted", "false", {"alerted": True})
    """
    logger.info(
        f"update_query called: {table}, {filter_column}={filter_value}, updates={updates}"
    )
    err = _check_table(table)
    if err:
        return {"error": err}
    try:
        response = (
            supabase.table(table)
            .update(updates)
            .eq(filter_column, filter_value)
            .execute()
        )
        logger.info(f"update_query success: {len(response.data)} rows updated")
        return {
            "status": "success",
            "rows_updated": len(response.data),
            "data": response.data,
        }
    except Exception as e:
        logger.error(f"update_query failed: {e}")
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    mcp.run()
