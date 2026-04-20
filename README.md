# shalin_langchain_project

Slack-based Q&A chatbot over a synthetic B2B SaaS corpus, built as the
take-home for the LangChain Applied AI Engineer role.

Users `@`-mention the bot in a Slack channel with a question about the
corpus (customer calls, support tickets, internal docs, competitor research
for a fictional startup called **Northstar Signal**). The bot replies in a
thread, grounds every answer in the database via 5 tools exposed to a
LangGraph ReAct agent, and supports multi-turn follow-ups in the same
thread without needing another `@`-mention.

- See [DESIGN.md](./DESIGN.md) for architecture and reasoning.
- See [`tests/eval_examples.py`](./tests/eval_examples.py) for the agent's
  pass rate on the assignment's 7 example queries, and
  [`tests/eval_extended.py`](./tests/eval_extended.py) for 8 additional
  probe queries covering the rest of the schema.

## Quick start

For a reviewer who already has a Slack workspace + app set up:

```bash
git clone <this repo>
cd shalin_langchain_project
python -m venv .venv
source .venv/bin/activate            # Windows: .venv/Scripts/activate
pip install -e .
cp .env.example .env
# Fill in OPENAI_API_KEY, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET
python -m src.slack.app
```

Then `@`-mention the bot in any channel it's been invited to.

If you need to create the Slack workspace/app from scratch, see
[Slack app setup](#slack-app-setup) below.

## Prerequisites

- Python 3.11+ (tested on 3.12)
- A Slack workspace where you have admin rights (free tier is fine)
- An OpenAI API key (the default model is `gpt-4o`)

## Installation

```bash
git clone <this repo>
cd shalin_langchain_project
python -m venv .venv
source .venv/bin/activate            # Windows: .venv/Scripts/activate
pip install -e .
```

The `.sqlite` database is checked into the repo at
[`data/synthetic_startup.sqlite`](./data/synthetic_startup.sqlite) — no
separate download needed.

## Slack app setup

The bot uses **Socket Mode**, which opens an outbound WebSocket from your
machine to Slack. No public URL, no ngrok, no webhook verification in this
mode (signature verification is implemented at
[`src/slack/signatures.py`](./src/slack/signatures.py) for the HTTP Events
API path, but isn't exercised in the Socket Mode demo).

1. Go to <https://api.slack.com/apps> and click **Create New App** →
   **From scratch**. Give it a name (e.g. "Northstar QA Bot") and pick
   your target workspace.

2. In the app settings, go to **OAuth & Permissions**. Under **Scopes**
   → **Bot Token Scopes**, add:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `groups:history`
   - `im:history`
   - `mpim:history`

3. Scroll back up and click **Install to Workspace**. Approve the prompt.
   The page now shows a **Bot User OAuth Token** starting with `xoxb-...`
   — copy it for `SLACK_BOT_TOKEN`.

4. Go to **Socket Mode** in the left sidebar and toggle it on. When
   prompted, generate an **App-Level Token** with the `connections:write`
   scope. It starts with `xapp-...` — copy for `SLACK_APP_TOKEN`.

5. Go to **Event Subscriptions** → toggle on. Under **Subscribe to bot
   events**, add:
   - `app_mention`
   - `message.channels` (optional, if you want follow-ups in public
     channels without an `@`-mention)
   - `message.groups` (same, for private channels)

6. Go to **Basic Information** → **App Credentials** and copy the
   **Signing Secret** for `SLACK_SIGNING_SECRET`. (Only used if you
   switch to HTTP Events API mode later.)

7. Invite the bot to a channel: `/invite @your-bot-name` in the channel
   where you want to test.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Value |
|---|---|
| `OPENAI_API_KEY` | `sk-proj-...` from OpenAI |
| `SLACK_BOT_TOKEN` | `xoxb-...` from step 3 above |
| `SLACK_APP_TOKEN` | `xapp-...` from step 4 above |
| `SLACK_SIGNING_SECRET` | from step 6 above |
| `OPENAI_MODEL` | (optional) defaults to `gpt-4o` |

The `.env` file is gitignored. Do not commit secrets.

## Running the bot

```bash
source .venv/bin/activate            # if not already active
python -m src.slack.app
```

You should see:
```
Socket Mode connecting...
A new session (s_...) has been established
Bolt app is running!
```

In Slack, go to a channel the bot is in, and try one of the example queries:

> `@yourbot for Verdant Bay, what's the approved live patch window, and
> how do we roll back if validation fails?`

The bot will:
1. Post a progress message in a new thread:
   ```
   [22:18:23] :hourglass_flowing_sand: Looking into this...
   [22:18:24] :mag: Loading customer context — Verdant Bay
   [22:18:25] :mag: Reading artifact — art_fff67d92fe41
   [22:18:26] :brain: Analyzing...
   [22:18:29] :white_check_mark: Done
   ```
2. Reply in the thread with the final answer, tagging you.

Reply to the bot's thread (no `@`-mention needed) to continue the
conversation — the LangGraph checkpointer keys state on the Slack
`thread_ts`, giving multi-turn memory automatically.

## Testing

Two eval suites are included. Both run all their queries **in parallel**
using `asyncio.gather`, so wall time is `max(per_query)` (~6-8s) rather
than the sum (~30s).

```bash
# The 7 example queries from the assignment's Notion page
python -m tests.eval_examples

# 8 additional probe queries covering the rest of the schema
python -m tests.eval_extended

# Individual tool sanity checks (no LLM)
python -m tests.test_tools

# Run a single example query with streamed tool trace (good for debugging)
python -m tests.run_one easy-2-verdant-bay-patch
```

Current pass rate: **7/7 on the assignment queries**,
**7/8 on the extended queries** (the one fail is a genuine SQL bug on a
total+breakdown aggregation query; see DESIGN.md under Known Limitations).

## Project layout

```
src/
  config.py              # env loading via python-dotenv
  agent/
    graph.py             # LangGraph ReAct agent (create_react_agent + MemorySaver)
    tools.py             # 5 tools exposed to the agent
    prompts.py           # system prompt
  db/
    connection.py        # read-only SQLite connection factory
  slack/
    app.py               # Bolt Socket Mode app + event handlers
    signatures.py        # HMAC-SHA256 request verification (HTTP Events path)
    splitter.py          # 4000-char message splitter
    buffer.py            # per-message edit coalescer (rate-limit friendly)
    queue.py             # per-thread serial job queue (one run at a time)
    progress.py          # LangGraph event -> Slack status text

data/
  synthetic_startup.sqlite   # the provided DB, FTS5 index pre-built

tests/
  test_tools.py          # per-tool smoke tests
  eval_examples.py       # the 7 assignment example queries, parallel
  eval_extended.py       # 8 additional probe queries, parallel
  run_one.py             # single-query runner with streamed tool trace
```

## The 5 tools

| Tool | When the agent uses it |
|---|---|
| `search_artifacts(query, artifact_type=None, limit=5)` | FTS5 over all 250 artifacts. Topic/situation lookups. |
| `get_artifact(artifact_id)` | Full content of one artifact. For exact wording, specific procedures, verbatim quotes. |
| `get_customer_context(customer_name_or_id)` | Full 360 for one customer: profile + implementation + summaries of all 5 artifacts. Hot path for named-customer questions. |
| `list_customers(region=, industry=, account_health=, crm_stage=, product=, name_contains=)` | Structured customer filter with enum validation. For "which customers..." scoping. |
| `sql_query(query, limit=50)` | Read-only SQL escape hatch for cross-table aggregations and edge cases. Gated: `SELECT`/`WITH` only, no semicolons, DDL/DML keyword blocklist, connection opened in SQLite read-only mode. |

## Security notes

- The SQLite connection is always opened in read-only mode
  (`file:...?mode=ro` URI), enforced at the driver level.
- `sql_query` additionally validates that the statement starts with
  `SELECT` or `WITH`, has no semicolons, and doesn't contain DDL/DML
  keywords. Row count is capped at 500.
- All non-`sql_query` tools use parameterized queries.
- `OPENAI_API_KEY` is held only by the `ChatOpenAI` client; it is never
  passed to tools, never logged, never echoed to the user.
- `.env` is gitignored. `.env.example` is the only committed env file and
  contains no secrets.
- When running under HTTP Events API (not the default Socket Mode path),
  every request is verified via HMAC-SHA256 using the signing secret
  (see `src/slack/signatures.py` and `DESIGN.md` for details).

## Troubleshooting

**"not_in_channel" or the bot doesn't respond:** the bot isn't in the
channel. Run `/invite @your-bot-name` in the channel.

**Socket Mode connection fails on startup:** check that `SLACK_APP_TOKEN`
starts with `xapp-` (not `xoxb-`) and has the `connections:write` scope.

**`ModuleNotFoundError: src`:** you're running from the wrong directory
or haven't done `pip install -e .`. Run from the repo root with the venv
activated.

**The bot responds but the answer is wrong or shallow:** the remaining
known failure modes are documented in DESIGN.md. For a quick debug, run
`python -m tests.run_one <example-id>` to see the tool-call trace.
