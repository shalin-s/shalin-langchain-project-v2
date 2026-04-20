"""Extended eval: 8 additional queries covering tables and tool paths that
the assignment's 7 example queries don't exercise.

Coverage targets:
  - employees table (no dedicated tool -> sql_query escape hatch)
  - products table (descriptions, use cases)
  - competitors table (strengths/weaknesses JSON)
  - company_profile table (mission, differentiation)
  - financial aggregation (SUM/GROUP BY via sql_query)
  - cross-table join (implementations + customers for contract sort)
  - messy-status LIKE patterns (implementations.status)
  - internal_communication artifact deep read (non-support-ticket content)

Run with: python -m tests.eval_extended
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.agent.graph import build_agent

EXAMPLES = [
    {
        "id": "ext-1-employee-lookup",
        "query": "Who is Jin Park at Northstar, and what role do they play?",
        "must_contain": [
            [r"jin park", r"\bjin\b"],
            [r"solutions engineer", r"solutions engineering"],
        ],
    },
    {
        "id": "ext-2-product-catalog",
        "query": "What are the four Northstar products, and give a one-line description of each.",
        "must_contain": [
            [r"signal ingest"],
            [r"event nexus"],
            [r"orchestrator"],
            [r"signal insights"],
        ],
    },
    {
        "id": "ext-3-total-acv-by-region",
        "query": "What's our total annual contract value across all customer implementations, and which region contributes the most?",
        "must_contain": [
            # Allow rounded figures; the real total is 23,229,000
            [r"23[,.]?2\d\d[,.]?000", r"\$?23\.?2\d?M", r"\$?23M"],
            [r"north america west"],
        ],
    },
    {
        "id": "ext-4-top-5-by-contract-value",
        "query": "Show me our top 5 implementations by contract value, with the customer name and current status.",
        "must_contain": [
            [r"arcadia cloudworks", r"arcadia"],
            [r"maplebridge"],
            [r"pioneer grid"],
            # At least one clearly stated contract value
            [r"1,?800,?000", r"\$1\.8M", r"1\.25M", r"1,?250,?000"],
        ],
    },
    {
        "id": "ext-5-competitor-profile",
        "query": "Give me a competitor profile for ObservaGrid: segment, pricing position, main strengths, and main weaknesses.",
        "must_contain": [
            [r"observability"],
            [r"premium"],
            [r"tracing", r"diagnostics"],
            [r"higher cost", r"weaker automation", r"cost at scale"],
        ],
    },
    {
        "id": "ext-6-at-risk-rollouts",
        "query": "How many of our implementations are currently stalled, at risk, or in some form of remediation? List the customers involved.",
        "must_contain": [
            # Ground truth: 16 match these status patterns
            [r"\b1[4-9]\b", r"\b16\b", r"\b15\b", r"\b17\b"],
            # At least a handful of customer names should be in the listing
            [r"maplebridge", r"maplefork", r"verdant bay", r"aureum", r"blueharbor"],
        ],
    },
    {
        "id": "ext-7-internal-comm-deep-read",
        "query": "In the MapleHarvest Quebec pilot's internal Slack thread about the schema transform, what did the team decide and what's the action item?",
        "must_contain": [
            [r"transform", r"mapping"],
            [r"schema"],
            [r"workshop", r"action item", r"sign[- ]?off", r"signoff"],
        ],
    },
    {
        "id": "ext-8-northstar-positioning",
        "query": "What's Northstar Signal's mission and main differentiator versus other observability vendors?",
        "must_contain": [
            [r"observab"],
            [r"event intelligence", r"event-intelligence", r"event processing"],
            [r"ingestion", r"correlat", r"enrichment", r"automation", r"runbook"],
        ],
    },
]


def _truncate(s: str, n: int = 120) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def _check_hits(answer: str, groups: list[list[str]]) -> dict:
    lower = answer.lower()
    return {
        group[0]: any(re.search(pat, lower, re.IGNORECASE) for pat in group)
        for group in groups
    }


async def run_one(agent, example: dict) -> dict:
    thread_id = f"ext-{example['id']}-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 16}

    tool_calls: list[tuple[str, dict]] = []
    final_answer = ""
    t0 = time.time()

    async for chunk in agent.astream(
        {"messages": [("user", example["query"])]},
        config=config,
        stream_mode="updates",
    ):
        for _node, payload in chunk.items():
            for m in payload.get("messages", []):
                if hasattr(m, "tool_calls") and m.tool_calls:
                    for tc in m.tool_calls:
                        tool_calls.append((tc["name"], tc.get("args", {})))
                elif (
                    m.__class__.__name__ == "AIMessage"
                    and not getattr(m, "tool_calls", None)
                ):
                    final_answer = (
                        m.content if isinstance(m.content, str) else str(m.content)
                    )

    hits = _check_hits(final_answer, example["must_contain"])
    return {
        "id": example["id"],
        "tool_calls": tool_calls,
        "n_tool_calls": len(tool_calls),
        "final_answer": final_answer,
        "hits": hits,
        "passed": all(hits.values()),
        "elapsed_s": round(time.time() - t0, 1),
    }


async def main() -> None:
    agent = build_agent()
    t_all = time.time()

    print(f"Launching {len(EXAMPLES)} extended queries in parallel...\n")
    tasks = [asyncio.create_task(run_one(agent, ex)) for ex in EXAMPLES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total = time.time() - t_all

    for ex, r in zip(EXAMPLES, results):
        print(f"\n{'#' * 70}\n# {ex['id']}\n{'#' * 70}")
        print(f"Q: {ex['query']}")
        if isinstance(r, Exception):
            print(f"ERROR: {r!r}")
            continue
        print(f"-- {r['elapsed_s']}s, {r['n_tool_calls']} tool calls")
        for name, args in r["tool_calls"]:
            print(f"   {name}({_truncate(json.dumps(args, default=str), 140)})")
        print(f"\n-- answer:\n{r['final_answer']}\n")
        print(f"-- expected: {r['hits']}  PASS={r['passed']}")

    print(f"\n{'=' * 70}\nSUMMARY (wall time: {total:.1f}s)")
    for ex, r in zip(EXAMPLES, results):
        if isinstance(r, Exception):
            print(f"  {ex['id']}: ERROR {r!r}")
        else:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  {ex['id']}: {mark}   ({r['n_tool_calls']} tool calls, {r['elapsed_s']}s)")


if __name__ == "__main__":
    asyncio.run(main())
