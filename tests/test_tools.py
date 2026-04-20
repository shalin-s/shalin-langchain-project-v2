"""Quick smoke test: invoke each tool with a plausible input and print results.

Run with: python -m tests.test_tools
"""
import json
from src.agent.tools import (
    search_artifacts,
    get_artifact,
    get_customer_context,
    list_customers,
    sql_query,
)


def banner(label: str):
    print(f"\n{'=' * 10} {label} {'=' * 10}")


def show(obj, limit_chars: int = 600):
    s = json.dumps(obj, indent=2, default=str)
    if len(s) > limit_chars:
        s = s[:limit_chars] + f"\n... [{len(s) - limit_chars} more chars]"
    print(s)


if __name__ == "__main__":
    banner("search_artifacts: 'taxonomy rollout'")
    results = search_artifacts.invoke({"query": "taxonomy rollout", "limit": 3})
    show(results)

    banner("search_artifacts: 'patch window' filtered to internal_document")
    results2 = search_artifacts.invoke(
        {"query": "patch window", "artifact_type": "internal_document", "limit": 3}
    )
    show(results2)

    banner("get_artifact on first result above")
    if results2 and "artifact_id" in results2[0]:
        full = get_artifact.invoke({"artifact_id": results2[0]["artifact_id"]})
        show(full, limit_chars=1200)

    banner("get_customer_context: 'BlueHarbor'")
    ctx = get_customer_context.invoke({"customer_name_or_id": "BlueHarbor"})
    show(ctx, limit_chars=2000)

    banner("list_customers: region=North America West")
    cust = list_customers.invoke({"region": "North America West", "limit": 5})
    show(cust)

    banner("sql_query: customers by region")
    agg = sql_query.invoke(
        {"query": "SELECT region, COUNT(*) AS n FROM customers GROUP BY region"}
    )
    show(agg)

    banner("sql_query: forbidden write should be rejected")
    bad = sql_query.invoke({"query": "DROP TABLE customers"})
    show(bad)
