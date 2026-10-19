import os

# Must be set before the app is imported: tiny hash embeddings (no model download), no API keys.
os.environ.update(
    EMBED_BACKEND="hash", CACHE_DIR="/tmp/contexta_test_cache", ARXIV_ENABLED="false",
    MIN_SCORE="0.0", RATE_LIMIT_PER_MIN="1000", RERANK_MODEL="none",
)
# Set (not pop!) to an empty string: app.config's load_dotenv() only fills env vars that are
# *absent* from os.environ, so a real .env file would silently refill a popped key the moment
# app.config is first imported. An empty string stays "present" and _env() treats it as unset.
for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL"):
    os.environ[k] = ""
