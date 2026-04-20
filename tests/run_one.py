"""Run a single example query with live tool-call output and timing.

Usage: python -m tests.run_one [easy-1-blueharbor-taxonomy]
"""
import sys
import time

from src.agent.graph import build_agent
from tests.eval_examples import EXAMPLES


def main() -> None:
    target_id = sys.argv[1] if len(sys.argv) > 1 else "easy-2-verdant-bay-patch"
    ex = next((e for e in EXAMPLES if e["id"] == target_id), None)
    if ex is None:
        print(f"unknown id: {target_id}")
        print("available:", [e["id"] for e in EXAMPLES])
        return

    print(f"Q: {ex['query']}\n")
    agent = build_agent()
    config = {"configurable": {"thread_id": f"run-one-{int(time.time())}"}}

    t0 = time.time()
    n_tool_calls = 0
    final = ""

    for chunk in agent.stream(
        {"messages": [("user", ex["query"])]},
        config=config,
        stream_mode="updates",
    ):
        elapsed = time.time() - t0
        for node, payload in chunk.items():
            msgs = payload.get("messages", [])
            for m in msgs:
                if hasattr(m, "tool_calls") and m.tool_calls:
                    for tc in m.tool_calls:
                        n_tool_calls += 1
                        args_short = str(tc.get("args", {}))[:100]
                        print(f"[{elapsed:5.1f}s] tool_call: {tc['name']}({args_short})")
                elif m.__class__.__name__ == "ToolMessage":
                    content = str(m.content)[:80].replace("\n", " ")
                    print(f"[{elapsed:5.1f}s]   -> {content}...")
                elif m.__class__.__name__ == "AIMessage":
                    if not getattr(m, "tool_calls", None):
                        final = m.content if isinstance(m.content, str) else str(m.content)
                        print(f"[{elapsed:5.1f}s] FINAL ANSWER:\n{final}")

    total = time.time() - t0
    print(f"\n--- {n_tool_calls} tool calls, {total:.1f}s total ---")
    hits = {sub: (sub.lower() in final.lower()) for sub in ex["must_contain"]}
    print(f"--- expected substrings: {hits}")


if __name__ == "__main__":
    main()
