# DESIGN.md v6

## 1. Stack choices

- LangGraph over DeepAgents
  - Mature, official, well-documented.
  - create_react_agent prebuilt gives the exact ReAct thing I want, no fuss.
- Slack Bolt Async + Socket Mode
- MemorySaver since this is a demo, could switch to SQL saver easily
- Model: gpt-4o at temp 0, seems to work fine satisfactorily.

---

## 2. Data stuff to note

- Three-way correspondence: 50 scenarios, 50 customers,50 implementations, all distinct
  - Every scenario has exactly 1 customer and 1 implementation.
  - Every customer shows up in exactly 1 scenario
  - "Scenario" and "customer" are equivalent IDs pointing at the same situation.
  - Implication: "scenario" is an internal artifact. LLM never needs to see the word. That's why the tool is get_customer_context, not get_scenario.
- 5 artifacts per scenario, one of each type.
  - Types: customer_call, support_ticket, internal_communication, internal_document, competitor_research.
  - Uniform across all 50 scenarios
  - Each type carries different info, we filter on artifact_type when searching.
- implementations.status is messy free form text.
  - About 30 variants across 50 rows ("stalled - reset recommended", "pilot - remediation active", etc).
  - Exact equality misses most rows.
  - Prompt tells the agent to use OR'd "LIKE" patterns on status.
- Several JSON-blob columns. Tools parse them to dicts / lists before returning so the LLM doesn't have to.
- FTS5 index is pre-built on artifacts.title + summary + content_text. So I can use that, don't need to worry about embeddings myself

---

## 3. Tools

- Three typical "shapes" of queries in general:
  1. Named-customer lookup ("for X, what's the approved patch window?").
  2. Discovery by detail ("which customer had the data-sync outage?").
  3. Cross-customer aggregation ("which region has the most at-risk customers on product Y?").
- Tool/process for each shape
  - Shape 1 → get_customer_context, plus get_artifact if exact wording matters.
  - Shape 2 → search_artifacts to narrow, then get_artifact to deep-read.
  - Shape 3 → list_customers for simple filters, sql_query for cross-table aggregation.

So the tools I specified are as follows:
### search_artifacts(query, artifact_type=None, limit=5)

- FTS5 over the three text columns.
- Returns id + type + customer name + snippet.
- Each result includes customer_id so the agent can use that in get_customer_context without another lookup.

### get_artifact(artifact_id)

- Full content for a single artifact.
- metadata_json parsed before return.
- Generally called after search_artifacts narrows things down, or when a question wants exact wording from the artifact details.

### get_customer_context(customer_name_or_id)

- "Get everything" tool: customer row + implementation row + summaries of all (5) artifacts
- Accepts a partial name or an id.
- Ambiguous name handling: if partial name matches multiple customers, return a list with a hint to retry with a narrower string, that should be better than returning one arbitrary match (silently wrong) or erroring out (no new info).
- Name is intentionally get_customer_context, not get_scenario. Since customer and scenario are equivalent here, don't need to confuse agent with the terminology of "scenario"

### list_customers(region, industry, account_health, crm_stage, product, name_contains)

- Structured filter with typed kwargs.
- Each enum arg validated against a whitelist.
- Invalid values return a structured error with valid_values instead of an empty list (to avoid confusing the agent with an empty list)

### sql_query(query, limit=50)

- Read-only SQL query, fallback option.
- Schema embedded in the tool info so the LLM sees it without a separate call needed.
- Row cap + statement validator + mode=ro judt for safety.
- Tradeoff: include this at all?
  - "LLM writes whatever SQL it wants" sounds scary.
  - Counter: defensive measures (see security)
  - Payoff: cross-customer aggregation questions that'd take 3+ narrow-tool calls become one SQL query.
  - For the hardest eval queries (the Canada approval-bypass one), this tool is actually needed.

---

## 4. Multi-turn support

- Multi-turn explicitly needed per the project rubric. Simplest clean mechanism for this is to: map each Slack thread to a LangGraph thread_id, 1 to 1.
- thread_ts maps to thread_id.
  - First @-mention in a channel starts a thread rooted at the mention's ts.
  - Follow-ups reuse that thread_ts and flow into the same LangGraph state.
- Memory comes for free.
  - Memory Saver persists message history per thread_id.
  - Agent sees prior turns automatically, no manual rehydration on our side.
- Active-threads set.
  - In-memory set of thread_ts values the bot "active" in.
  - Only handle messages in threads we're actually supposed to be inolved in
  - Keeps the bot from replying to every random message in every channel it's in.
- Per-thread serial queue.
  - Follow-up while the previous turn is still running makes the message queued, not parallel. Two concurrent runs on the same thread_id would be problematic, like obviously, it's supposed to be one continuous thread.

---

## 5. Slack bot UX for interactions with user

- Problem: Slack has no native streaming. But, users waiting 10+ seconds with no visible progress would assume the bot is broken.
- Solution: post an immediate placeholder, then edit it as LangGraph events fire (astream with stream_mode="updates").
- What the user sees (timestamped lines appended to the placeholder):
  ```
  [22:18:23] Looking into this...
  [22:18:24] Loading customer context - Verdant Bay
  [22:18:25] Reading artifact - art_fff67d92fe41
  [22:18:26] Analyzing...
  [22:18:29] Done
  ```
- Rate-limit protection.
  - chat.update is rate-limited per channel and per message.
  - Editing on every LangGraph event would 429 or silently drop updates.
  - MessageBuffer queues edits per (channel, ts), flushes on a 1s tick, keeps only the latest payload.
  - Ultimately, user sees the latest state at a reasonably intervl, and we don't exhaust the rate limit and get 429s.
- Give final answer as a separate message that tags the asker for clarity.
  - Progress trail stays visible for debugging / transparency (could be turned off later if desired)
  - Clean final answer is easier to read than if it were crammed in with the other stuff.

---

## 6. Security/reliability

### Webhook validation

- HMAC-SHA256 verifier with 5-minute replay window at src/slack/signatures.py.
- Uses hmac.compare_digest for constant-time comparison.
- Socket Mode demo path has no HTTP webhooks to verify. WebSocket  authenticated by the App-level Token.

### Tool auth

- Credentials.
  - 5 tools only touch a local read-only SQLite file. No external API creds at the tool layer.
  - OPENAI_API_KEY is held by the ChatOpenAI client (not a tool). Loaded from .env, never logged, never passed as a tool arg.
- Sandboxing of tool invocations:
  - Dangerous tool is sql_query. LLM writing arbitrary SQL is an injection surface.
  - Four layers of defense:
    1. Read-only URI (mode=ro). Driver-level. Even DROP TABLE errors before touching disk.
    2. Statement must start with SELECT or WITH.
    3. No semicolons. Blocks chaining.
    4. DDL / DML keyword blocklist.
  - Other tools use parameterized queries.

---

## 7. Agent performance notes/eval process

- Starting state: agent worked, but some queries looped, some picked wrong answers, some answered from general knowledge without grounding.
- Iteration loop:
  - Loop on evals automatically (7 assignment queries + 8 I wrote later)
	  - look at tool traces
	  - tune prompts or tools
	  - rerun.
  - Cycles took 30-60 seconds because eval queries run in parallel via asyncio.gather.
- Fixes worth narrating:
  1. FTS sanitizer.
     - Raw dashes in dates / hyphenated terms made FTS5 either error or apply unintended NOT operators.
     - Fixed by splitting on non-word chars: 2026-02-20 turns into "2026 02 20", duplicate-action turns into duplicate action.
     - Boolean operators still work normally.
  2. Empty results vs errors.
     - My original list_customers with an invalid filter used to return empty list.
     - Agent would retry with the same args. Watched it get stuck on identical calls before giving up.
     - Now returns a structured error with valid_values. Agent uses the hint and moves on.
  3. Multi-criteria comparison.
     - Hardest failure: "which customer is most likely to defect?" The agent grabbed the first at-risk customer instead of comparing all candidates' competitor framing.
     - Fix: a worked example in the prompt telling the agent to  evaluate candidates with all criteria and pick the one with the MOST explicit chrun risk description.
- Final numbers on eval set:
  - 7/7 on the assignment queries across multiple runs.
  - 7/8 on the extended set.
  - Tool-call counts: 1-5 per query, not too many.

---

## 8. Limitations and Next Steps

- Could expand evalset and do better on more/complex queries, hill-climbing on that
- Could add more functionalities like chat forking, stopping/interupted, etc that would be in a typical thing like chatgpt.
- 
---

## 9. Running / testing reference

- python -m src.slack.app - boots the bot.
- python -m tests.eval_examples - runs the 7 eval queries from the assignment description in parallel with full tool traces
- python -m tests.eval_extended - runs 8 additional evalqueries I wrote.
- python -m tests.run_one easy-2-verdant-bay-patch - fastest single-query debug loop; streams tool calls as they happen.

---
