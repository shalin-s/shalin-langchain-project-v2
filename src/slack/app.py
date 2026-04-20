"""Slack Bolt app entry point.

Wires the Slack event loop to the LangGraph agent over Socket Mode (no public
webhook needed). Each Slack thread maps to a LangGraph thread_id, giving
multi-turn memory for free via the agent's checkpointer.

Run with: python -m src.slack.app
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agent.graph import build_agent
from src.config import SLACK_APP_TOKEN, SLACK_BOT_TOKEN
from src.slack.buffer import MessageBuffer
from src.slack.progress import (
    initial_status,
    status_for_tool_start,
    thinking_status,
)
from src.slack.queue import ThreadQueue
from src.slack.splitter import split_for_slack

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*")


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def build_app() -> tuple[AsyncApp, MessageBuffer]:
    app = AsyncApp(token=SLACK_BOT_TOKEN)
    agent = build_agent()
    thread_queue = ThreadQueue()
    active_threads: set[str] = set()

    async def _update_message(channel: str, ts: str, text: str) -> None:
        await app.client.chat_update(channel=channel, ts=ts, text=text)

    buffer = MessageBuffer(_update_message)

    async def run_agent_turn(
        channel: str, thread_ts: str, user_id: str, user_text: str
    ) -> None:
        """One agent turn: stream progress as a cumulative log, then post the
        final answer in a separate message that tags the asker."""
        steps: list[tuple[str, str]] = [
            (datetime.now().strftime("%H:%M:%S"), initial_status())
        ]

        def _render() -> str:
            return "\n".join(f"`[{t}]` {c}" for t, c in steps)

        placeholder = await app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=_render(),
        )
        ts = placeholder["ts"]
        final_text = ""

        def _append_step(line: str) -> None:
            if steps and steps[-1][1] == line:
                return
            steps.append((datetime.now().strftime("%H:%M:%S"), line))
            buffer.queue_edit(channel, ts, _render())

        try:
            async for chunk in agent.astream(
                {"messages": [("user", user_text)]},
                config={
                    "configurable": {"thread_id": thread_ts},
                    "recursion_limit": 16,
                },
                stream_mode="updates",
            ):
                for _node, payload in chunk.items():
                    for m in payload.get("messages", []):
                        if hasattr(m, "tool_calls") and m.tool_calls:
                            for tc in m.tool_calls:
                                _append_step(
                                    status_for_tool_start(
                                        tc["name"], tc.get("args", {})
                                    )
                                )
                        elif m.__class__.__name__ == "AIMessage" and not getattr(
                            m, "tool_calls", None
                        ):
                            content = m.content
                            final_text = (
                                content if isinstance(content, str) else str(content)
                            )
                            _append_step(thinking_status())

            _append_step(":white_check_mark: Done")
            await asyncio.sleep(1.1)  # let buffer flush last edit

            mention = f"<@{user_id}> " if user_id else ""
            answer = final_text or "_(no answer produced)_"
            chunks = split_for_slack(f"{mention}{answer}")
            for c in chunks:
                await app.client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts, text=c
                )
        except Exception as e:
            logger.exception("agent turn failed for thread %s", thread_ts)
            _append_step(f":warning: Error: `{e}`")
            await asyncio.sleep(1.1)
            try:
                await app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"<@{user_id}> :warning: Sorry, something went wrong: `{e}`",
                )
            except Exception:
                pass

    @app.event("app_mention")
    async def on_mention(event, logger):
        text = _strip_mentions(event.get("text", ""))
        if not text:
            return
        channel = event["channel"]
        user_id = event.get("user", "")
        # If mentioned inside an existing thread, continue it; otherwise start
        # a new thread rooted at this mention.
        thread_ts = event.get("thread_ts") or event["ts"]
        active_threads.add(thread_ts)
        thread_queue.submit(
            thread_ts,
            lambda: run_agent_turn(channel, thread_ts, user_id, text),
        )

    @app.event("message")
    async def on_message(event, logger):
        # Skip bot messages, edits, deletions, etc.
        if event.get("bot_id") or event.get("subtype"):
            return
        thread_ts = event.get("thread_ts")
        if not thread_ts or thread_ts not in active_threads:
            return
        text = _strip_mentions(event.get("text", ""))
        if not text:
            return
        channel = event["channel"]
        user_id = event.get("user", "")
        thread_queue.submit(
            thread_ts,
            lambda: run_agent_turn(channel, thread_ts, user_id, text),
        )

    return app, buffer


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app, buffer = build_app()
    await buffer.start()
    try:
        handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
        print("Socket Mode connecting...")
        await handler.start_async()
    finally:
        await buffer.stop()


if __name__ == "__main__":
    asyncio.run(main())
