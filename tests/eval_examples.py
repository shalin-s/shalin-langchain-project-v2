"""Run the agent against the assignment's example queries in parallel.

The DB is read-only and each asyncio task gets its own sqlite connection
via the tools' context managers, so parallel execution is safe. Most of
the wall time is OpenAI API latency, which parallelizes well with asyncio.

Usage: python -m tests.eval_examples
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time

# Ensure Unicode output works on Windows consoles (cp1252 default).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.agent.graph import build_agent

# Each must_contain entry is a list of regex alternatives; the answer must
# match at least one pattern from each group. Patterns are case-insensitive.
EXAMPLES = [
    {
        "id": "easy-1-blueharbor-taxonomy",
        "query": "which customer's issue started after the 2026-02-20 taxonomy rollout, and what proof plan did we propose to get them comfortable with renewal?",
        "must_contain": [
            [r"blueharbor"],
            [r"proof[- ]of[- ]fix", r"proof plan", r"7[- ]?10 business days?"],
            [r"a/b", r"a-b", r"\bab test\b"],
        ],
    },
    {
        "id": "easy-2-verdant-bay-patch",
        "query": "for Verdant Bay, what's the approved live patch window, and exactly how do we roll back if the validation checks fail?",
        "must_contain": [
            [r"2026-03-24", r"march 24,? 2026"],
            [r"02:00", r"2:00", r"2am", r"2 am"],
            [r"rollback", r"roll[- ]back"],
            [r"orchestrator rollback", r"prior_sha", r"prior ruleset"],
        ],
    },
    {
        "id": "easy-3-mapleharvest-quebec",
        "query": "in the MapleHarvest Quebec pilot, what temporary field mappings are we planning in the router transform, and what is the March 23 workshop supposed to produce?",
        "must_contain": [
            [r"txn_id"],
            [r"transaction_id"],
            [r"schema"],
        ],
    },
    {
        "id": "easy-4-aureum-scim",
        "query": "what SCIM fields were conflicting at Aureum, and what fast fix did Jin propose so we don't have to wait on Okta change control?",
        "must_contain": [
            [r"department"],
            [r"businessunit", r"business[- ]unit"],
            [r"\bjin\b"],
        ],
    },
    {
        "id": "hard-1-defection-risk",
        "query": "which customer looks most likely to defect to a cheaper tactical competitor if we miss the next promised milestone, and what exactly is that milestone?",
        "must_contain": [
            [r"blueharbor"],
            [r"noiseguard"],
            [r"proof[- ]of[- ]fix", r"7[- ]?10 business days?", r"a/b", r"saved searches"],
        ],
    },
    {
        "id": "hard-2-na-west-event-nexus",
        "query": "among the North America West Event Nexus accounts, which ones are really dealing with taxonomy/search semantics problems versus duplicate-action problems?",
        "must_contain": [
            [r"taxonomy"],
            [r"duplicate"],
            # At least one of each group from ground truth
            [r"arcadia", r"blueharbor", r"cedarwind", r"heliofab", r"pacific health", r"pioneer freight"],
            [r"helix", r"ledgerbright", r"ledgerpeak", r"medlogix", r"peregrine", r"pioneer grid"],
        ],
    },
    {
        "id": "hard-3-canada-approval-bypass",
        "query": "do we have a recurring Canada approval-bypass pattern across accounts, or is MapleBridge basically a one-off? Give me the customer names and the shared failure pattern in plain English.",
        "must_contain": [
            [r"maplebridge"],
            [r"maplefork"],
            [r"approval"],
            [r"rule", r"precedence", r"override"],
        ],
    },
]


def _truncate(s: str, n: int = 120) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def _check_hits(answer: str, groups: list[list[str]]) -> dict:
    lower = answer.lower()
    result = {}
    for group in groups:
        key = group[0]
        result[key] = any(re.search(pat, lower, re.IGNORECASE) for pat in group)
    return result


async def run_one(agent, example: dict) -> dict:
    thread_id = f"eval-{example['id']}-{int(time.time())}"
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
        "query": example["query"],
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

    print(f"Launching {len(EXAMPLES)} queries in parallel...\n")
    tasks = [asyncio.create_task(run_one(agent, ex)) for ex in EXAMPLES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = time.time() - t_all

    for ex, r in zip(EXAMPLES, results):
        print(f"\n{'#' * 70}\n# {ex['id']}\n{'#' * 70}")
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
