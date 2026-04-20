"""Map LangGraph stream events to user-friendly progress text for Slack.

The Slack bot subscribes to LangGraph's astream_events and uses this module
to translate event payloads into the placeholder message the user sees.
"""
from typing import Any

TOOL_LABELS = {
    "search_artifacts": "Searching artifacts",
    "get_artifact": "Reading artifact",
    "get_customer_context": "Loading customer context",
    "list_customers": "Filtering customers",
    "sql_query": "Querying database",
}


def initial_status() -> str:
    return ":hourglass_flowing_sand: Looking into this..."


def status_for_tool_start(tool_name: str, tool_input: dict[str, Any]) -> str:
    label = TOOL_LABELS.get(tool_name, tool_name)
    hint = _short_input_hint(tool_name, tool_input)
    return f":mag: {label}{(' — ' + hint) if hint else '...'}"


def _short_input_hint(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "search_artifacts":
        q = str(tool_input.get("query", ""))[:60]
        return f'"{q}"' if q else ""
    if tool_name == "get_customer_context":
        return str(tool_input.get("customer_name_or_id", ""))[:60]
    if tool_name == "get_artifact":
        return str(tool_input.get("artifact_id", ""))[:30]
    return ""


def thinking_status() -> str:
    return ":brain: Analyzing..."
