# HANDOFF SPEC — `submit_knowledge_graph_feedback` tool

> **📥 INBOUND SPEC — implement in THIS repo**
>
> **Origin:** `highway-snowflake` (`~/Documents/Repos/highway-snowflake`),
> design discussion 2026-08-07
> **Companion doc (origin repo):** `docs/superpowers/specs/2026-08-07-snowflake-kg-parity-design.md`
>
> `highway-snowflake` owns the knowledge graph and the skill that *calls* this tool.
> **This repo owns the tool itself** — the implementation described below belongs
> here, in `snowflake-mcp-server`.
>
> Questions about knowledge-graph semantics, the skill's calling behavior, or how
> filed issues get triaged → those belong to `highway-snowflake`, not here.

---

## Why this tool exists

The Snowflake knowledge graph is distributed to the organization as a plugin
bundling the (read-only) Snowflake MCP server plus a skill. When a power user tells
the skill its answer was wrong — a bad join, a missing table, absent business
context — that correction is the highest-value signal available for improving the
graph. Today there is no channel to capture it.

## Hard constraints (non-negotiable)

1. **Snowflake stays strictly read-only.** `ALLOWED_SQL_COMMANDS` in
   `snowflake_mcp_server/config.py` must remain
   `["select","show","describe","explain","with","union","use"]`. Do **not** add
   `call`, `insert`, or anything else. Feedback is never written to Snowflake.
2. **The Jira token must never leave the server process.** Not in a return value,
   not in a log line, not in an error message, not in an exception trace. It must
   never reach model context or a conversation transcript.
3. **No credential ships in the plugin bundle.** The token is read from Snowflake
   at call time.
4. **No new MCP server or connector dependency.** The org should not need the
   Atlassian connector installed for this to work.

## Architecture

```
Skill calls  submit_knowledge_graph_feedback(structured args)
        ↓
MCP SERVER (this project's code):
   1. SELECT token FROM <kg secrets table>   ← internal only; never returned
   2. build Jira issue payload
   3. HTTPS POST /rest/api/3/issue → project HS
   4. return { issue_key, issue_url }        ← no credential in response
        ↓
Jira project HS  (https://listreports.atlassian.net/jira/core/projects/HS)
   = STORE OF RECORD for knowledge-graph feedback
```

### Token storage

A knowledge-graph secrets table in Snowflake holds the Jira API token in a single
row. **The value is populated manually by Rob via the Snowflake web UI — never by
code.**

Note: a native Snowflake `SECRET` object is deliberately **not** used. `SECRET`s are
unreadable via `SELECT` by design; this flow requires the server to read the value
directly, so a table column is the correct mechanism.

## Tool contract

The tool must be **self-describing** so the model knows exactly when to use it and,
just as importantly, when not to.

```
name: submit_knowledge_graph_feedback

description:
  Report that the Snowflake knowledge graph returned incorrect, incomplete, or
  missing information — a wrong join, an undocumented table, a missing
  relationship, or bad metadata. Creates a Jira issue in project HS for triage.
  Does NOT modify Snowflake or the knowledge graph directly; it files a work item
  a human reviews. Use only when a user explicitly indicates the answer was wrong
  or incomplete — not for ordinary follow-up questions.

args:
  feedback_type         enum: wrong_relationship | missing_table
                            | missing_relationship | incorrect_metadata
                            | data_quality_issue
  affected_fqn          fully-qualified table name(s) involved
  user_description      what the user said was wrong (verbatim where possible)
  suggested_correction  optional structured hint:
                          { source, target, join_key, business_purpose }
  original_question     the question that produced the bad answer (context)

returns:
  { issue_key: "HS-42",
    issue_url: "https://listreports.atlassian.net/browse/HS-42" }
```

`suggested_correction` matters most downstream: automated inference can propose join
candidates and measure empirical match rates, but it cannot produce
`business_purpose` or institutional warnings. This field is the channel through
which human meaning reaches the graph — capture it as structure, not prose.

## Server-side requirements

- Token read into a local variable; scrubbed from any log/error path
- Validate and sanitize all inputs before placing them in the Jira payload
- Jira unreachable → return a clear error to the caller; do not swallow it
- Per-session submission cap to prevent flooding HS
- Jira credential should be a **dedicated service account scoped to create-issue on
  HS only** — defense in depth, since the primary control is that the token never
  leaves the process

## Acceptance criteria

- [ ] `ALLOWED_SQL_COMMANDS` unchanged; `execute_query` still rejects non-read SQL
- [ ] Tool creates a real issue in HS and returns its key + URL
- [ ] Token appears in no return value, log, or error message (verify by grep of
      captured output during a forced-failure test)
- [ ] Jira-unreachable path returns a clear error rather than silently dropping
- [ ] Tool description causes the model to invoke it on explicit user correction and
      NOT on ordinary follow-ups
- [ ] No credential present anywhere in the packaged `.mcpb` bundle

## Out of scope for the MCP server project

Applying corrections to the knowledge graph. Filed issues are triaged by a human;
approved corrections are applied by `highway-snowflake`'s trusted server-side
pipeline. The MCP server's responsibility ends when the Jira issue is created.
