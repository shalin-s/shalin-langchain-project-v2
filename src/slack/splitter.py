"""Split long text into Slack-safe chunks.

Slack's chat.postMessage caps plain text at ~4000 chars. Block Kit allows
larger messages but with per-block 3000-char text limits. This splitter
prefers line breaks, then word breaks, then hard cuts.
"""

DEFAULT_MAX_CHARS = 3500  # leave headroom under the 4000-char hard cap


def split_for_slack(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n", 0, max_chars)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)

    n = len(chunks)
    if n > 1:
        chunks = [f"{c}\n_({i + 1}/{n})_" for i, c in enumerate(chunks)]
    return chunks
