# PostgreSQL Explorer Skill (Local)

Use these tools to explore the PostgreSQL database configured for this workspace.

## Key Tools
- `list_schemas`: List all non-system schemas.
- `list_tables`: List tables in a specific schema.
- `describe_table`: Get columns and indexes for a table.
- `run_query_limited`: Execute read-only queries with safety limits.
- `optimize_query_skill`: Run a one-shot optimization for a SQL query.

## Best Practices
- Always explore the schema before writing complex queries.
- Use `optimize_query_skill` when the user asks to improve query performance.
- Default to `public` schema if not specified.
