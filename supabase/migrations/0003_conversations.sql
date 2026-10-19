-- Server-side conversation history for workspace Q&A (P2-14, ADR-006).
--
-- Owner-approved design (2026-10-19):
--   * visible only to the user who asked - not to other members, not to workspace admins;
--   * deleted 30 days after the last turn (HISTORY_RETENTION_DAYS; expired threads are also
--     hidden on every read, so retention holds even before the worker's purge runs);
--   * each turn stores the question, answer, citations and the full source passages it was
--     answered from, so a thread stays readable after a document changes or is deleted.

CREATE TABLE conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner_idx ON conversations (workspace_id, user_id, updated_at DESC);
CREATE INDEX conversations_updated_at_idx ON conversations (updated_at);  -- retention purge

CREATE TABLE conversation_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    position int NOT NULL,
    question text NOT NULL,
    standalone_question text,  -- the follow-up as rewritten for retrieval, when it differed
    answer text NOT NULL,
    outcome text NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]',  -- full passages: document_id, filename, page_number, section_heading, text, score
    citations jsonb NOT NULL DEFAULT '[]',
    claims jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, position)
);

GRANT ALL ON conversations, conversation_turns TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, conversation_turns TO authenticated;

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns ENABLE ROW LEVEL SECURITY;

-- Author only, and only while still a member of the workspace. Admins get no extra access.
CREATE POLICY own_conversations ON conversations
    FOR ALL USING (
        user_id = auth.uid()
        AND workspace_id IN (SELECT workspace_id FROM memberships WHERE user_id = auth.uid())
    )
    WITH CHECK (
        user_id = auth.uid()
        AND workspace_id IN (SELECT workspace_id FROM memberships WHERE user_id = auth.uid())
    );

CREATE POLICY own_conversation_turns ON conversation_turns
    FOR ALL USING (conversation_id IN (SELECT id FROM conversations WHERE user_id = auth.uid()))
    WITH CHECK (conversation_id IN (SELECT id FROM conversations WHERE user_id = auth.uid()));
