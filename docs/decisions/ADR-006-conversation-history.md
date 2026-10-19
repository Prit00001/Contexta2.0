# ADR-006: Stored conversation history for workspace Q&A

| | |
|---|---|
| Status | Accepted — design decisions made by the project owner, 2026-10-19 |
| Date | 2026-10-19 |
| Scope | Contexta 2.0, `/workspaces/{id}/ask` and `/workspaces/{id}/conversations` (backlog P2-14) |
| Builds on | P2-14 stateless follow-ups (`core/followup.py`), PRD_PHASE_2 §2 (P2-14 blocked on an approved storage design) |

## Context

Follow-up questions already work statelessly: the client sends up to three earlier turns as
`history`, a `contextualize` node rewrites the follow-up into a standalone question, and nothing is
stored. The PRD left server-side history blocked until the owner approved who can see it, how long
it is kept and what is stored. Workspace users are signed-in people with documents behind access
groups, so stored threads are new personal data next to tenant data.

## Decision

The owner chose (2026-10-19):

1. **Visible only to the author.** A thread belongs to the user who asked. Other members and
   workspace admins cannot list, read, continue or delete it. Enforced twice: every query in
   `core/conversations.py` is scoped to `(workspace_id, user_id)` (the API connects as the service
   role), and RLS policy `own_conversations` gives the same rule to `authenticated` connections,
   including losing access when the author leaves the workspace.
2. **30-day retention after the last turn** (`HISTORY_RETENTION_DAYS`, default 30). Expired threads
   are hidden on every read, so retention holds even before deletion runs; the worker
   (`services/worker.py`) deletes them hourly. Each new turn restarts the clock. Users can delete a
   thread at any time (`DELETE /workspaces/{id}/conversations/{cid}`). Deleting the workspace or the
   user deletes their threads (`ON DELETE CASCADE`).
3. **Each turn stores the question, the rewritten standalone question, the answer, outcome,
   citations, claims and the full source passages** it was answered from, so a thread stays readable
   after its documents change or are deleted.

Mechanics:

- `POST /workspaces/{id}/ask` with `save: true` starts a thread (created only after the answer, so a
  failed ask leaves nothing behind) and returns `conversation_id`; sending `conversation_id` continues
  it, with the server supplying the last three turns as history. `history` and `conversation_id` are
  mutually exclusive.
- API keys have no user identity, so they cannot store threads (403); they can still send `history`.
- Only questions and answers are fed back to the model as history - never stored passages. Retrieval
  for every turn runs fresh under the caller's current access groups.

## Consequences

- **Stored passages outlive their documents and the author's access** (the owner's chosen
  trade-off): if a document is deleted, or the author is removed from the access group that could
  see it, the passages already stored in *that author's own* threads stay readable to them until the
  thread expires (≤ 30 days idle) or they delete it. No one else gains access. Removing the author
  from the workspace hides all their threads immediately (`require_role` and RLS).
- New tables `conversations` and `conversation_turns` (migration `0003_conversations.sql`).
- Erasure requests are served by deleting the user (cascade) or their threads; nothing is copied
  elsewhere (no logging of question or answer text, no tracing attributes - see CONFIGURATION.md).
- Not built: sharing a thread, server-side history for the public `/ask` demo (no user identity),
  a UI for listing threads.
