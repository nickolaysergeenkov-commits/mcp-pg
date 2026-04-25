# PostgreSQL MCP Server (Cursor)

Dedicated MCP project for connecting Cursor to PostgreSQL for:
- schema exploration
- table/index inspection
- report query building
- query optimization workflows

## Features

Tools exposed by `mcp_pg_server.py`:
- `list_schemas`
- `list_tables(schema)`
- `describe_table(schema, table)`
- `list_indexes(schema)`
- `explain_query(sql, analyze=False, buffers=False)`
- `run_query_limited(sql, limit=100, timeout_ms=5000)`
- `optimize_query_iteration(sql, candidate_sql="", sample_limit=100, analyze=False, buffers=False)`
- `optimize_query_skill(sql, candidate_sql="", sample_limit=100, analyze=False, buffers=False)`

## Install

```bash
pip install -r requirements.txt
```

## Database config

Set one of:
- `POSTGRES_DSN` (recommended)
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`

Example:

```bash
export POSTGRES_DSN="host=127.0.0.1 port=5432 dbname=app user=app_readonly password=secret"
```

## Run

Cursor mode (stdio):

```bash
python mcp_pg_server.py --transport stdio
```

Local MCP client mode (SSE):

```bash
python mcp_pg_server.py --transport sse
```

## Global Cursor Usage (Across Multiple Workspaces)

To use this MCP server in every Cursor project, add it to your **Global Cursor Settings**:

1.  Open Cursor and go to **Settings** -> **General** -> **MCP**.
2.  Click **+ Add New MCP Server**.
3.  **Name**: `postgres-explorer` (or any name you prefer).
4.  **Type**: `command`.
5.  **Command**:
    ```bash
    python /absolute/path/to/mcp-pg/mcp-pg-server/mcp_pg_server.py --transport stdio
    ```
    *(Ensure you use the absolute path to the script and your python interpreter/venv if needed).*
6.  **Environment Variables**: Add `POSTGRES_DSN` or other credentials as needed.

Once added, the database tools will be available in the Cursor Chat and Composer across all your workspaces.

## Cursor MCP config example

```json
{
  "mcpServers": {
    "postgres": {
      "command": "python",
      "args": ["/absolute/path/to/pg-mcp-server/mcp_pg_server.py", "--transport", "stdio"],
      "env": {
        "POSTGRES_DSN": "host=127.0.0.1 port=5432 dbname=app user=app_readonly password=secret"
      }
    }
  }
}
```

## One-shot optimization usage

Prompt Cursor:

> Use `optimize_query_skill` with my SQL and return the best candidate query for performance.
