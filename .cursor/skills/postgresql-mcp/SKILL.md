---
name: postgresql-mcp
description: >-
  Explores and queries PostgreSQL through the project MCP server using list_schemas,
  list_tables, describe_table, run_query_limited, and optimize_query_skill. Use when
  working in mcp-pg or any workspace with this PostgreSQL MCP, when exploring schemas
  or tables, writing or reviewing SQL, doing read-only data exploration, or when the
  user asks for query performance improvements or execution-plan analysis.
---

# PostgreSQL MCP

## Tools

| Tool | Use |
|------|-----|
| `list_schemas` | Discover available schemas. |
| `list_tables` | List tables; default schema is `public` if unspecified. |
| `describe_table` | Column types, constraints, and indexes before writing SQL. |
| `run_query_limited` | Read-only queries; respects server safety limits and timeouts. |
| `optimize_query_skill` | Performance tuning and execution-plan recommendations. |

## Workflow

1. **Explore first** — Call `describe_table` (after `list_schemas` / `list_tables` as needed) before proposing or running non-trivial SQL.
2. **Safety** — Use only the MCP tools above for database access in this project; do not substitute ad-hoc clients unless the user explicitly opts out.
3. **Optimization** — If a query is slow or the user asks for performance help, use `optimize_query_skill` on the relevant SQL.

## Checklist

- [ ] Schemas/tables known or listed via MCP
- [ ] Table structure confirmed with `describe_table` before complex queries
- [ ] Read-only exploration via `run_query_limited`
- [ ] `optimize_query_skill` used when performance is in scope
