// Mirrors app/schemas.py. Keep in step with the API; /docs on a running server is the reference.

export type Outcome = "answered" | "extractive_fallback" | "insufficient_evidence";

export interface Source {
  id: string;
  title: string;
  source: "local" | "arxiv" | "pdf" | string;
  url: string;
  score: number;
  snippet: string;
  authors: string[];
  published: string;
  page: number | null;
  marker: number;
  document_id: string;
  chunk_id: string;
}

export interface Claim {
  text: string;
  markers: number[];
  status: "cited" | "uncited";
  support?: "supported" | "not_supported" | "unknown" | null;
  support_method?: "quote" | "numbers" | "llm_judge" | null;
  unsupported_numbers?: string[] | null;
}

export interface CitationCheck {
  verification: "marker_only" | "local_checks" | "claim_support";
  valid_markers: number;
  removed_markers: number[];
  uncited_claims: number;
  supported_claims?: number | null;
  not_supported_claims?: number | null;
  unknown_claims?: number | null;
}

export interface WorkflowStep {
  stage: string;
  status: string;
  duration_ms: number;
}

/** Fields every answer carries, whichever endpoint produced it. */
export interface AnswerFields {
  answer: string;
  outcome: Outcome;
  claims: Claim[];
  citation_check?: CitationCheck; // not stored with conversation turns
  standalone_question?: string | null;
  query_rewrites?: string[];
  warnings?: string[];
}

export interface AskResponse extends AnswerFields {
  question: string;
  sources: Source[];
  model_used: string;
  provider: string;
  llm_used: boolean;
  cached: boolean;
  diagnostics: string[];
  timings_ms: Record<string, number>;
  request_id: string;
  workflow: WorkflowStep[];
}

export interface Turn {
  question: string;
  answer: string;
}

export interface Health {
  status: string;
  index_size: number;
  llm_providers: string[];
  arxiv_enabled: boolean;
}

// ---- workspaces ----

export type Role = "viewer" | "member" | "admin";

export interface AuthConfig {
  workspaces_enabled: boolean;
  supabase_url: string | null;
  supabase_anon_key: string | null;
  history_retention_days: number;
}

export interface Workspace {
  workspace_id: string;
  name: string;
  role: Role;
  org_id: string;
}

export interface DocumentRow {
  id: string;
  filename: string;
  status: "queued" | "processing" | "ready" | "failed" | string;
  error: string | null;
  created_at: string;
}

export interface WorkspaceSource {
  document_id: string;
  filename: string;
  page_number: number | null;
  section_heading: string | null;
  snippet?: string;
  text?: string; // stored turns keep the full passage
  score: number;
}

export interface WorkspaceAskResponse extends AnswerFields {
  question: string;
  sources: WorkspaceSource[];
  provider: string;
  model_used: string;
  llm_used: boolean;
  conversation_id: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns: number;
}

export interface StoredTurn extends AnswerFields {
  position: number;
  question: string;
  sources: WorkspaceSource[];
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  turns: StoredTurn[];
}

export interface Member {
  user_id: string;
  email: string | null;
  role: Role;
  created_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

// ---- billing (ADR-007) ----

export interface Billing {
  enabled: boolean;
  plan: "unlimited" | "trial" | "active" | "past_due" | "expired";
  trial_seconds_left: number;
  trial_seconds_total: number;
  seats: number;
  seats_billed: number | null;
  subscription_status: string | null;
  current_end: string | null;
  price_label: string;
  can_manage: boolean;
}
