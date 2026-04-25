import argparse
import json
import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Dict, List

import psycopg
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-pg")

mcp = FastMCP("postgres-explorer")


def _dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if dsn:
        return dsn

    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    dbname = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    if not dbname or not user:
        raise ValueError(
            "Missing database credentials. Set POSTGRES_DSN or PGDATABASE + PGUSER (and optional PGHOST/PGPORT/PGPASSWORD)."
        )

    parts = [f"host={host}", f"port={port}", f"dbname={dbname}", f"user={user}"]
    if password:
        parts.append(f"password={password}")
    return " ".join(parts)


@contextmanager
def _connect():
    conn = psycopg.connect(_dsn())
    try:
        yield conn
    finally:
        conn.close()


def _validate_identifier(value: str, label: str) -> str:
    if not value:
        raise ValueError(f"{label} is required")
    if any(ch in value for ch in ('"', "'", ";", "\\", "\x00")):
        raise ValueError(f"{label} contains invalid characters")
    return value


def _normalize_sql(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _is_read_only_sql(sql: str) -> bool:
    normalized = _normalize_sql(sql).lower()
    if not normalized:
        return False

    if re.search(
        r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|copy|call|vacuum|refresh|analyze)\b",
        normalized,
    ):
        return False

    return normalized.startswith(("select", "with", "values", "show", "explain"))


def _ensure_read_only_sql(sql: str, label: str = "sql") -> str:
    if not sql or not sql.strip():
        raise ValueError(f"{label} is required")
    normalized = _normalize_sql(sql)
    if not _is_read_only_sql(normalized):
        raise ValueError(f"{label} must be a read-only SELECT-style statement")
    return normalized


def _extract_plan_root(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, list) and plan and isinstance(plan[0], dict):
        return plan[0].get("Plan", {})
    if isinstance(plan, dict):
        return plan.get("Plan", {})
    return {}


def _walk_plan_nodes(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = [node]
    for child in node.get("Plans", []) or []:
        if isinstance(child, dict):
            nodes.extend(_walk_plan_nodes(child))
    return nodes


def _build_plan_findings(plan_root: Dict[str, Any]) -> Dict[str, Any]:
    if not plan_root:
        return {"summary": "No plan root found.", "signals": [], "recommendations": []}

    nodes = _walk_plan_nodes(plan_root)
    seq_scans = [n for n in nodes if n.get("Node Type") == "Seq Scan"]
    sorts = [n for n in nodes if n.get("Node Type") in {"Sort", "Incremental Sort"}]
    nested_loops = [n for n in nodes if n.get("Node Type") == "Nested Loop"]
    hash_joins = [n for n in nodes if n.get("Node Type") == "Hash Join"]

    recommendations: List[str] = []
    signals: List[str] = []

    if seq_scans:
        signals.append(f"Detected {len(seq_scans)} sequential scan node(s).")
        recommendations.append(
            "Review WHERE/JOIN predicates on scanned tables and consider supporting indexes."
        )
    if sorts:
        signals.append(f"Detected {len(sorts)} sort node(s).")
        recommendations.append(
            "If sorting large sets, consider indexes aligned with ORDER BY keys or reduce rows earlier."
        )
    if nested_loops and len(nodes) > 3:
        signals.append("Nested Loop appears in a multi-node plan.")
        recommendations.append(
            "Check join selectivity and indexes on join keys; consider rewriting joins for better cardinality filtering."
        )
    if hash_joins:
        signals.append(f"Detected {len(hash_joins)} hash join node(s).")
        recommendations.append(
            "Verify memory settings and join input sizes; pre-filter large sides when possible."
        )

    root_cost = plan_root.get("Total Cost")
    root_rows = plan_root.get("Plan Rows")
    node_type = plan_root.get("Node Type")
    summary = f"Root node: {node_type}, estimated rows: {root_rows}, total cost: {root_cost}."

    if not recommendations:
        recommendations.append(
            "Plan looks reasonable at root level. Validate with ANALYZE and test on realistic parameters."
        )

    return {
        "summary": summary,
        "signals": signals,
        "recommendations": recommendations,
        "node_count": len(nodes),
    }


@mcp.tool()
def list_schemas() -> List[str]:
    """List non-system PostgreSQL schemas."""
    query = """
    SELECT schema_name
    FROM information_schema.schemata
    WHERE schema_name NOT IN ('information_schema')
      AND schema_name NOT LIKE 'pg_%'
    ORDER BY schema_name;
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query)
        return [r[0] for r in cur.fetchall()]


@mcp.tool()
def list_tables(schema: str = "public") -> List[Dict[str, Any]]:
    """List tables/views in a schema with row estimate."""
    schema = _validate_identifier(schema, "schema")
    query = """
    SELECT
      n.nspname AS schema_name,
      c.relname AS table_name,
      CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'p' THEN 'partitioned table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized view'
        WHEN 'f' THEN 'foreign table'
        ELSE c.relkind::text
      END AS relation_type,
      c.reltuples::bigint AS estimated_rows
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s
      AND c.relkind IN ('r','p','v','m','f')
    ORDER BY c.relname;
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query, (schema,))
        rows = cur.fetchall()
    return [
        {
            "schema": r[0],
            "name": r[1],
            "type": r[2],
            "estimated_rows": r[3],
        }
        for r in rows
    ]


@mcp.tool()
def describe_table(schema: str, table: str) -> Dict[str, Any]:
    """Describe columns, constraints, and indexes for a table."""
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")

    columns_query = """
    SELECT
      column_name,
      data_type,
      is_nullable,
      column_default,
      ordinal_position
    FROM information_schema.columns
    WHERE table_schema = %s
      AND table_name = %s
    ORDER BY ordinal_position;
    """

    constraints_query = """
    SELECT
      tc.constraint_name,
      tc.constraint_type,
      string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS columns
    FROM information_schema.table_constraints tc
    LEFT JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
     AND tc.table_name = kcu.table_name
    WHERE tc.table_schema = %s
      AND tc.table_name = %s
    GROUP BY tc.constraint_name, tc.constraint_type
    ORDER BY tc.constraint_type, tc.constraint_name;
    """

    indexes_query = """
    SELECT
      i.relname AS index_name,
      idx.indisunique AS is_unique,
      idx.indisprimary AS is_primary,
      pg_get_indexdef(idx.indexrelid) AS definition
    FROM pg_class t
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_index idx ON t.oid = idx.indrelid
    JOIN pg_class i ON i.oid = idx.indexrelid
    WHERE n.nspname = %s
      AND t.relname = %s
    ORDER BY i.relname;
    """

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(columns_query, (schema, table))
        columns = [
            {
                "name": r[0],
                "data_type": r[1],
                "nullable": r[2] == "YES",
                "default": r[3],
                "position": r[4],
            }
            for r in cur.fetchall()
        ]

        cur.execute(constraints_query, (schema, table))
        constraints = [
            {"name": r[0], "type": r[1], "columns": r[2] or ""}
            for r in cur.fetchall()
        ]

        cur.execute(indexes_query, (schema, table))
        indexes = [
            {
                "name": r[0],
                "is_unique": r[1],
                "is_primary": r[2],
                "definition": r[3],
            }
            for r in cur.fetchall()
        ]

    return {
        "schema": schema,
        "table": table,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


@mcp.tool()
def list_indexes(schema: str = "public") -> List[Dict[str, Any]]:
    """List indexes in a schema and their usage stats."""
    schema = _validate_identifier(schema, "schema")
    query = """
    SELECT
      schemaname,
      tablename,
      indexname,
      indexdef,
      idx_scan,
      idx_tup_read,
      idx_tup_fetch
    FROM pg_indexes pi
    LEFT JOIN pg_stat_user_indexes psi
      ON pi.schemaname = psi.schemaname
      AND pi.tablename = psi.relname
      AND pi.indexname = psi.indexrelname
    WHERE pi.schemaname = %s
    ORDER BY pi.tablename, pi.indexname;
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query, (schema,))
        rows = cur.fetchall()

    return [
        {
            "schema": r[0],
            "table": r[1],
            "index": r[2],
            "definition": r[3],
            "idx_scan": r[4],
            "idx_tup_read": r[5],
            "idx_tup_fetch": r[6],
        }
        for r in rows
    ]


@mcp.tool()
def explain_query(sql: str, analyze: bool = False, buffers: bool = False) -> Dict[str, Any]:
    """Run EXPLAIN (optionally ANALYZE) and return JSON query plan."""
    normalized = _ensure_read_only_sql(sql)

    options = ["FORMAT JSON"]
    if analyze:
        options.append("ANALYZE true")
    if buffers:
        options.append("BUFFERS true")

    query = f"EXPLAIN ({', '.join(options)}) {normalized}"

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query)
        plan = cur.fetchone()[0]

    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            pass

    return {
        "analyze": analyze,
        "buffers": buffers,
        "plan": plan,
    }


@mcp.tool()
def run_query_limited(sql: str, limit: int = 100, timeout_ms: int = 5000) -> Dict[str, Any]:
    """
    Execute a read-only query in safe preview mode:
    - enforces statement timeout
    - returns at most `limit` rows
    """
    normalized = _ensure_read_only_sql(sql)

    if limit <= 0 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    if timeout_ms <= 0 or timeout_ms > 120000:
        raise ValueError("timeout_ms must be between 1 and 120000")

    wrapped_sql = f"SELECT * FROM ({normalized}) AS mcp_preview LIMIT {int(limit)}"

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
        cur.execute(wrapped_sql)
        rows = cur.fetchall()
        columns = [desc.name for desc in cur.description]

    return {
        "columns": columns,
        "row_count": len(rows),
        "rows": [list(row) for row in rows],
        "limit": limit,
        "timeout_ms": timeout_ms,
    }


@mcp.tool()
def optimize_query_iteration(
    sql: str,
    candidate_sql: str = "",
    sample_limit: int = 100,
    analyze: bool = False,
    buffers: bool = False,
) -> Dict[str, Any]:
    """
    Iteration helper for Cursor:
    1) EXPLAIN current query
    2) optional EXPLAIN candidate query
    3) run limited preview for current/candidate query
    """
    current_sql = _ensure_read_only_sql(sql, "sql")
    current_plan = explain_query(current_sql, analyze=analyze, buffers=buffers)
    current_preview = run_query_limited(current_sql, limit=sample_limit)

    result: Dict[str, Any] = {
        "current": {
            "sql": current_sql,
            "plan_summary": _extract_plan_root(current_plan.get("plan")),
            "preview": current_preview,
        },
        "next_step": "Revise SQL based on plan_summary, then rerun optimize_query_iteration.",
    }

    if candidate_sql and candidate_sql.strip():
        revised_sql = _ensure_read_only_sql(candidate_sql, "candidate_sql")
        revised_plan = explain_query(revised_sql, analyze=analyze, buffers=buffers)
        revised_preview = run_query_limited(revised_sql, limit=sample_limit)
        result["candidate"] = {
            "sql": revised_sql,
            "plan_summary": _extract_plan_root(revised_plan.get("plan")),
            "preview": revised_preview,
        }
        result["next_step"] = (
            "Compare current and candidate plan summaries/latency, revise candidate_sql, and repeat."
        )

    return result


@mcp.tool()
def optimize_query_skill(
    sql: str,
    candidate_sql: str = "",
    sample_limit: int = 100,
    analyze: bool = False,
    buffers: bool = False,
) -> Dict[str, Any]:
    """
    One-shot Cursor skill for report query optimization.
    Runs explain + review + limited preview so users don't need to ask each step manually.
    """
    iteration = optimize_query_iteration(
        sql=sql,
        candidate_sql=candidate_sql,
        sample_limit=sample_limit,
        analyze=analyze,
        buffers=buffers,
    )

    current_plan_summary = iteration["current"]["plan_summary"]
    result: Dict[str, Any] = {
        "workflow": "create -> explain -> review -> optimize -> preview limited rows -> repeat",
        "current": {
            "sql": iteration["current"]["sql"],
            "plan_review": _build_plan_findings(current_plan_summary),
            "preview": iteration["current"]["preview"],
        },
        "suggested_next_action": "Apply one recommendation, revise SQL, and run optimize_query_skill again.",
    }

    if "candidate" in iteration:
        candidate_plan_summary = iteration["candidate"]["plan_summary"]
        result["candidate"] = {
            "sql": iteration["candidate"]["sql"],
            "plan_review": _build_plan_findings(candidate_plan_summary),
            "preview": iteration["candidate"]["preview"],
        }
        result["suggested_next_action"] = (
            "Compare current vs candidate reviews/previews and keep the faster, lower-cost variant."
        )

    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PostgreSQL MCP server")
    parser.add_argument(
        "--transport",
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        choices=["stdio", "sse"],
        help="MCP transport to use. stdio for Cursor, sse for local testing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logger.info("Starting PostgreSQL MCP server with transport=%s", args.transport)
    mcp.run(transport=args.transport)
