import json
import re
import sqlite3
from typing import Optional

from langchain_core.tools import tool

from src.db.connection import get_connection

ARTIFACT_TYPES = {
    "customer_call",
    "support_ticket",
    "internal_communication",
    "internal_document",
    "competitor_research",
}

REGIONS = {"ANZ", "Canada", "Nordics", "North America West"}
ACCOUNT_HEALTHS = {"at risk", "expanding", "healthy", "recovering", "watch list"}
CRM_STAGES = {
    "active pilot",
    "escalation recovery",
    "expansion cycle",
    "implementation",
    "new logo pursuit",
    "renewal review",
}
PRODUCT_NAMES = {"Signal Ingest", "Event Nexus", "Orchestrator", "Signal Insights"}

_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts_query(query: str) -> str:
    """Make raw user text safe for FTS5 MATCH.

    FTS5 treats bare dashes, colons, parens, etc. as operator syntax, so
    tokens like '2026-02-20' or 'duplicate-action' either error or degrade
    to an unintended NOT. We split on non-word characters to yield plain
    word tokens (AND'd implicitly by FTS5), while preserving reserved
    boolean operators and user-supplied "quoted phrases".
    """
    out: list[str] = []
    pending_quote: list[str] = []
    in_quotes = False

    for tok in query.split():
        if tok.startswith('"') and tok.endswith('"') and len(tok) > 1:
            out.append(tok)
            continue
        if tok.startswith('"'):
            in_quotes = True
            pending_quote.append(tok)
            continue
        if in_quotes:
            pending_quote.append(tok)
            if tok.endswith('"'):
                out.append(" ".join(pending_quote))
                pending_quote = []
                in_quotes = False
            continue
        if tok.upper() in _FTS_OPERATORS:
            out.append(tok.upper())
            continue
        words = re.findall(r"[A-Za-z0-9_]+", tok)
        out.extend(words)

    if pending_quote:
        out.append(" ".join(pending_quote) + '"')
    return " ".join(out)

_JSON_FIELDS_BY_TABLE = {
    "customers": {"contacts_json"},
    "implementations": {"success_metrics_json", "risks_json"},
    "artifacts": {"metadata_json"},
    "products": {"deployment_modes_json", "core_use_cases_json", "features_json"},
    "competitors": {"strengths_json", "weaknesses_json"},
}


def _row_to_dict(row, table: Optional[str] = None) -> dict:
    out = dict(row)
    json_fields = _JSON_FIELDS_BY_TABLE.get(table, set())
    for k in list(out.keys()):
        if k in json_fields and out[k]:
            try:
                out[k.removesuffix("_json")] = json.loads(out[k])
                del out[k]
            except json.JSONDecodeError:
                pass
    return out


def _resolve_customer_id(conn, name_or_id: str) -> list[dict]:
    if re.fullmatch(r"cus_[0-9a-f]+", name_or_id):
        rows = conn.execute(
            "SELECT customer_id, name FROM customers WHERE customer_id = ?",
            (name_or_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT customer_id, name FROM customers WHERE name LIKE ? ORDER BY name",
            (f"%{name_or_id}%",),
        ).fetchall()
    return [dict(r) for r in rows]


@tool
def search_artifacts(
    query: str,
    artifact_type: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Full-text search across the artifacts corpus (title, summary, content).

    Use this to find relevant documents when the user asks about a topic,
    problem, event, or situation without naming a specific customer.

    Args:
        query: FTS5 query string. Supports boolean operators (AND, OR, NOT)
            and phrase queries ("quoted phrase"). Examples: 'taxonomy rollout',
            '"patch window"', 'SCIM AND Okta'.
        artifact_type: Optional filter. One of: customer_call, support_ticket,
            internal_communication, internal_document, competitor_research.
            Use this to narrow the search when you know what kind of document
            likely holds the answer (e.g., internal_document for formal
            procedures, support_ticket for technical errors).
        limit: Max results to return. Default 5.

    Returns:
        List of matches, each with artifact_id, artifact_type, title, summary,
        a highlighted snippet, customer_id, customer_name, scenario_id, and
        created_at. Use get_artifact(artifact_id) to fetch full content.
    """
    if artifact_type and artifact_type not in ARTIFACT_TYPES:
        return [{"error": f"invalid artifact_type; must be one of {sorted(ARTIFACT_TYPES)}"}]

    safe_query = _sanitize_fts_query(query)
    sql = """
        SELECT a.artifact_id, a.artifact_type, a.title, a.summary, a.created_at,
               a.customer_id, a.scenario_id,
               c.name AS customer_name,
               snippet(artifacts_fts, 2, '<<', '>>', '...', 20) AS snippet
        FROM artifacts_fts
        JOIN artifacts a ON a.artifact_id = artifacts_fts.artifact_id
        LEFT JOIN customers c ON c.customer_id = a.customer_id
        WHERE artifacts_fts MATCH ?
    """
    params: list = [safe_query]
    if artifact_type:
        sql += " AND a.artifact_type = ?"
        params.append(artifact_type)
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(1, min(limit, 20)))

    with get_connection() as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            return [{"error": f"FTS query error: {e}"}]
    return [dict(r) for r in rows]


@tool
def get_artifact(artifact_id: str) -> dict:
    """Fetch the full content of a single artifact by ID.

    Use this after search_artifacts finds a promising match and you need the
    exact wording, numbers, or technical details that aren't in the summary.

    Args:
        artifact_id: Artifact identifier like 'art_abc123...'.

    Returns:
        Full artifact row with content_text, summary, title, type, created_at,
        and parsed metadata. Metadata schema varies by artifact_type.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if not row:
            return {"error": f"no artifact with id {artifact_id}"}
        return _row_to_dict(row, "artifacts")


@tool
def get_customer_context(customer_name_or_id: str) -> dict:
    """Get the full 360-degree context for a single customer.

    Returns the customer profile, their implementation/rollout details, and
    summaries of all 5 artifacts associated with them (one of each type:
    customer_call, support_ticket, internal_communication, internal_document,
    competitor_research).

    Use this when the user names a specific customer, or after search_artifacts
    has identified the relevant customer. For detailed artifact content, follow
    up with get_artifact(artifact_id).

    Args:
        customer_name_or_id: Either a customer name (partial match supported,
            e.g., 'BlueHarbor') or an ID like 'cus_abc123...'.

    Returns:
        Dict with keys: customer, implementation, artifacts. Or an error/
        disambiguation response if the name matches 0 or >1 customers.
    """
    with get_connection() as conn:
        matches = _resolve_customer_id(conn, customer_name_or_id)
        if not matches:
            return {"error": f"no customer matches '{customer_name_or_id}'"}
        if len(matches) > 1:
            return {
                "error": "ambiguous name, multiple matches",
                "candidates": matches,
                "hint": "call again with a more specific name or the customer_id",
            }
        customer_id = matches[0]["customer_id"]

        customer_row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        impl_row = conn.execute(
            "SELECT * FROM implementations WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        artifact_rows = conn.execute(
            """SELECT artifact_id, artifact_type, title, summary, created_at
               FROM artifacts WHERE customer_id = ?
               ORDER BY created_at""",
            (customer_id,),
        ).fetchall()

    return {
        "customer": _row_to_dict(customer_row, "customers") if customer_row else None,
        "implementation": _row_to_dict(impl_row, "implementations") if impl_row else None,
        "artifacts": [dict(r) for r in artifact_rows],
    }


@tool
def list_customers(
    region: Optional[str] = None,
    industry: Optional[str] = None,
    account_health: Optional[str] = None,
    crm_stage: Optional[str] = None,
    product: Optional[str] = None,
    name_contains: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """List customers matching structured filters.

    Use this to scope down to a set of candidates for aggregate questions
    (e.g., 'which NA West accounts...', 'customers in Canada with X').

    Args:
        region: One of 'ANZ', 'Canada', 'Nordics', 'North America West'.
        industry: Industry name (partial match). Customer industries are
            business verticals (Logistics, SaaS, Retail, etc.), NOT
            Northstar's own products. Use the `product` filter instead if
            you mean 'customers using Signal Ingest / Event Nexus / etc.'.
        account_health: One of 'at risk', 'expanding', 'healthy',
            'recovering', 'watch list'.
        crm_stage: One of 'active pilot', 'escalation recovery',
            'expansion cycle', 'implementation', 'new logo pursuit',
            'renewal review'.
        product: Filter by Northstar product in the customer's implementation.
            One of 'Signal Ingest', 'Event Nexus', 'Orchestrator',
            'Signal Insights'. Matches the primary product_id on the
            implementation OR any product named in scope_summary prose.
        name_contains: Partial name match.
        limit: Max rows. Default 50 (the full customer count).

    Returns:
        List of customer rows with customer_id, name, industry, region,
        account_health, crm_stage, primary_contact_name. Use
        get_customer_context() to drill into any one of them. On an invalid
        enum value, returns a single-element list with an 'error' key and
        the accepted values.
    """
    validations = [
        ("region", region, REGIONS),
        ("account_health", account_health, ACCOUNT_HEALTHS),
        ("crm_stage", crm_stage, CRM_STAGES),
        ("product", product, PRODUCT_NAMES),
    ]
    for name, value, allowed in validations:
        if value is not None and value not in allowed:
            return [
                {
                    "error": f"invalid {name}: {value!r}",
                    "valid_values": sorted(allowed),
                    "hint": f"use one of the listed {name} values, or omit this filter",
                }
            ]

    where = ["1=1"]
    params: list = []
    join = ""
    if region:
        where.append("c.region = ?")
        params.append(region)
    if industry:
        where.append("c.industry LIKE ?")
        params.append(f"%{industry}%")
    if account_health:
        where.append("c.account_health = ?")
        params.append(account_health)
    if crm_stage:
        where.append("c.crm_stage = ?")
        params.append(crm_stage)
    if name_contains:
        where.append("c.name LIKE ?")
        params.append(f"%{name_contains}%")
    if product:
        join = (
            " LEFT JOIN implementations i ON i.customer_id = c.customer_id"
            " LEFT JOIN products p ON p.product_id = i.product_id"
        )
        where.append("(p.name = ? OR i.scope_summary LIKE ?)")
        params.extend([product, f"%{product}%"])

    sql = f"""SELECT DISTINCT c.customer_id, c.name, c.industry, c.subindustry,
                     c.region, c.country, c.size_band, c.account_health,
                     c.crm_stage, c.primary_contact_name
              FROM customers c{join}
              WHERE {' AND '.join(where)}
              ORDER BY c.name
              LIMIT ?"""
    params.append(max(1, min(limit, 200)))

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b",
    re.IGNORECASE,
)


@tool
def sql_query(query: str, limit: int = 50) -> list[dict]:
    """Run a read-only SQL query against the database.

    Use this for cross-table or aggregate questions that the other tools
    cannot express (e.g., 'recurring pattern across accounts',
    'highest contract value with at-risk status'). The connection is opened
    read-only, but the query is also validated to reject non-SELECT statements.

    Schema reference:
      customers(customer_id, scenario_id, name, industry, subindustry, region,
                country, size_band, employee_count, annual_revenue_band,
                crm_stage, tech_stack_summary, account_health,
                primary_contact_name, primary_contact_email, contacts_json, notes)
      implementations(implementation_id, scenario_id, customer_id, product_id,
                      deployment_model, status, kickoff_date, go_live_date,
                      contract_value, scope_summary, success_metrics_json,
                      risks_json)
      scenarios(scenario_id, created_at, industry, region, company_size_band,
                primary_product_id, secondary_product_id, primary_competitor_id,
                trigger_event, pain_point, scenario_summary, status)
      artifacts(artifact_id, scenario_id, customer_id, product_id,
                competitor_id, artifact_type, title, created_at, summary,
                content_text, token_estimate, metadata_json)
      products(product_id, name, category, description, target_persona,
               pricing_model)
      competitors(competitor_id, name, segment, description,
                  pricing_position)
      employees(employee_id, full_name, email, title, department, region,
                management_level)
      company_profile(company_id, name, category, headquarters, mission,
                      pricing_overview, differentiation)
      artifacts_fts (virtual FTS5 table over artifacts.title, summary,
                     content_text; join back on artifact_id)

    Args:
        query: A single SELECT or WITH statement. Must not contain semicolons
            or any DDL/DML keywords.
        limit: Row cap applied by this tool on top of any LIMIT in the query.

    Returns:
        List of row dicts, or a single-element list with an 'error' key.
    """
    q = query.strip().rstrip(";")
    if ";" in q:
        return [{"error": "multiple statements not allowed"}]
    if not re.match(r"^\s*(select|with)\b", q, re.IGNORECASE):
        return [{"error": "only SELECT / WITH queries are allowed"}]
    if _SQL_FORBIDDEN.search(q):
        return [{"error": "forbidden SQL keyword detected; only reads are allowed"}]

    row_cap = max(1, min(limit, 500))
    with get_connection() as conn:
        try:
            rows = conn.execute(q).fetchmany(row_cap)
        except Exception as e:
            return [{"error": f"SQL error: {e}"}]
    return [dict(r) for r in rows]


ALL_TOOLS = [
    search_artifacts,
    get_artifact,
    get_customer_context,
    list_customers,
    sql_query,
]
