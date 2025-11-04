import os


def get_bool(env_var: str, default: bool = False) -> bool:
    val = os.getenv(env_var)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def memory_enabled() -> bool:
    # Default to False to preserve legacy behavior unless explicitly enabled
    return get_bool("MEMORY_ENABLED", default=False)


def semantic_enabled() -> bool:
    return get_bool("SEMANTIC_RECALL_ENABLED", default=False)


def db_connection_string() -> str | None:
    # Prefer DATABASE_URL; fallback to SQLALCHEMY_DATABASE_URI
    return os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")


def is_postgres_connection(conn: str | None) -> bool:
    if not conn:
        return False
    return conn.startswith("postgres://") or conn.startswith("postgresql://")


def chat_table_name() -> str:
    return os.getenv("MEMORY_CHAT_TABLE", "langchain_memory")


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def semantic_top_k() -> int:
    try:
        return int(os.getenv("SEMANTIC_TOP_K", "5"))
    except Exception:
        return 5


def context_char_cap() -> int:
    try:
        return int(os.getenv("MEMORY_CONTEXT_CHAR_CAP", "12000"))
    except Exception:
        return 12000