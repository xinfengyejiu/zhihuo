"""轻量 schema 补丁:create_all 不会改已有表,这里补新增列/索引与数据迁移。"""

from sqlalchemy import text

from .database import engine


def ensure_schema() -> None:
    with engine.begin() as conn:
        dialect = engine.dialect.name
        if dialect == "sqlite":
            _sqlite_add_column(conn, "chat_sessions", "novel_id", "INTEGER")
            _sqlite_add_column(conn, "chat_sessions", "compressed_summary", "TEXT")
            _sqlite_add_column(conn, "chat_sessions", "product_key", "VARCHAR(32) DEFAULT 'novel'")
            _sqlite_add_column(conn, "chat_sessions", "workspace_id", "INTEGER")
            _sqlite_add_column(conn, "chat_messages", "chapter_id", "INTEGER")
            _sqlite_add_column(conn, "users", "last_product_key", "VARCHAR(32)")
            _sqlite_add_column(conn, "usage_logs", "product_key", "VARCHAR(32)")
            _sqlite_add_column(conn, "usage_logs", "workspace_id", "INTEGER")
            _sqlite_add_column(conn, "skills", "scope_tags", "TEXT DEFAULT '[]'")
            _sqlite_add_column(conn, "skills", "pack_dir", "VARCHAR(255)")
            _sqlite_add_column(conn, "knowledge_bases", "scope_tags", "TEXT DEFAULT '[]'")
            _sqlite_add_column(conn, "ai_models", "modality", "VARCHAR(16)")
            _sqlite_add_column(conn, "media_accounts", "app_id", "VARCHAR(128)")
            _sqlite_add_column(conn, "media_accounts", "app_secret_encrypted", "VARCHAR(1000)")
            _sqlite_add_column(conn, "media_accounts", "access_token_encrypted", "VARCHAR(2000)")
            _sqlite_add_column(conn, "media_accounts", "refresh_token_encrypted", "VARCHAR(2000)")
            _sqlite_add_column(conn, "media_accounts", "publish_api_base", "VARCHAR(500)")
            _sqlite_add_column(conn, "media_accounts", "token_note", "VARCHAR(255)")
            _sqlite_add_column(conn, "media_accounts", "token_status", "VARCHAR(16) DEFAULT 'not_configured'")
            _sqlite_add_column(conn, "media_accounts", "token_updated_at", "DATETIME")
            _sqlite_add_column(conn, "novels", "visual_style_prompt", "TEXT")
            _sqlite_add_column(conn, "characters", "visual_prompt", "TEXT")
            _sqlite_add_column(conn, "characters", "reference_image_urls", "TEXT DEFAULT '[]'")
            _sqlite_add_column(conn, "drama_shots", "start_sec", "INTEGER DEFAULT 0")
            _sqlite_add_column(conn, "drama_shots", "end_sec", "INTEGER DEFAULT 5")
            _sqlite_add_column(conn, "drama_shots", "shot_size", "VARCHAR(16)")
            _sqlite_add_column(conn, "drama_shots", "camera_move", "VARCHAR(16)")
            _sqlite_add_column(conn, "drama_shots", "angle", "VARCHAR(16)")
            _sqlite_add_column(conn, "drama_shots", "speaker", "VARCHAR(64)")
            _sqlite_add_column(conn, "drama_shots", "candidate_urls", "TEXT DEFAULT '[]'")
            _sqlite_add_column(conn, "drama_shots", "selected_index", "INTEGER DEFAULT 0")
            _sqlite_add_column(conn, "drama_shots", "refine_note", "TEXT")
            try:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_user_novel "
                        "ON chat_sessions(user_id, novel_id) WHERE novel_id IS NOT NULL"
                    )
                )
            except Exception:
                try:
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_user_novel "
                            "ON chat_sessions(user_id, novel_id)"
                        )
                    )
                except Exception:
                    pass
            _migrate_purposes(conn)
            _migrate_resource_mounts(conn)
            _backfill_defaults(conn)
        else:
            for stmt in (
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS novel_id INTEGER",
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS compressed_summary TEXT",
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS product_key VARCHAR(32) DEFAULT 'novel'",
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS workspace_id INTEGER",
                "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS chapter_id INTEGER",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_product_key VARCHAR(32)",
                "ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS product_key VARCHAR(32)",
                "ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS workspace_id INTEGER",
                "ALTER TABLE skills ADD COLUMN IF NOT EXISTS scope_tags JSON DEFAULT '[]'",
                "ALTER TABLE skills ADD COLUMN IF NOT EXISTS pack_dir VARCHAR(255)",
                "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS scope_tags JSON DEFAULT '[]'",
                "ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS modality VARCHAR(16)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS app_id VARCHAR(128)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS app_secret_encrypted VARCHAR(1000)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS access_token_encrypted VARCHAR(2000)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS refresh_token_encrypted VARCHAR(2000)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS publish_api_base VARCHAR(500)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS token_note VARCHAR(255)",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS token_status VARCHAR(16) DEFAULT 'not_configured'",
                "ALTER TABLE media_accounts ADD COLUMN IF NOT EXISTS token_updated_at TIMESTAMP",
                "ALTER TABLE novels ADD COLUMN IF NOT EXISTS visual_style_prompt TEXT",
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS visual_prompt TEXT",
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS reference_image_urls JSON DEFAULT '[]'",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS start_sec INTEGER DEFAULT 0",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS end_sec INTEGER DEFAULT 5",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS shot_size VARCHAR(16)",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS camera_move VARCHAR(16)",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS angle VARCHAR(16)",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS speaker VARCHAR(64)",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS candidate_urls JSON DEFAULT '[]'",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS selected_index INTEGER DEFAULT 0",
                "ALTER TABLE drama_shots ADD COLUMN IF NOT EXISTS refine_note TEXT",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
            _migrate_purposes(conn)
            _migrate_resource_mounts(conn)
            _backfill_defaults(conn)


def _sqlite_add_column(conn, table: str, column: str, coltype: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    names = {r[1] for r in rows}
    if column not in names:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


def _migrate_purposes(conn) -> None:
    """短用途名 → novel.*（已是带点的跳过）。"""
    try:
        rows = conn.execute(text("SELECT id, purpose FROM ai_model_routes")).fetchall()
    except Exception:
        return
    for rid, purpose in rows:
        if not purpose or "." in purpose:
            continue
        new_p = f"novel.{purpose}"
        try:
            conn.execute(
                text("UPDATE ai_model_routes SET purpose=:np WHERE id=:id"),
                {"np": new_p, "id": rid},
            )
        except Exception:
            pass


def _migrate_resource_mounts(conn) -> None:
    """从旧 novel_*_links 回填 resource_mounts。"""
    try:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS resource_mounts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    product_key VARCHAR(32) NOT NULL,
                    workspace_type VARCHAR(32) NOT NULL,
                    workspace_id INTEGER NOT NULL,
                    resource_type VARCHAR(16) NOT NULL,
                    resource_id INTEGER NOT NULL,
                    stage VARCHAR(32)
                )
                """
            )
        )
    except Exception:
        return

    # novel kb
    try:
        links = conn.execute(
            text(
                """
                SELECT n.owner_id, nkl.novel_id, nkl.kb_id
                FROM novel_kb_links nkl
                JOIN novels n ON n.id = nkl.novel_id
                """
            )
        ).fetchall()
        for owner_id, novel_id, kb_id in links:
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM resource_mounts
                    WHERE product_key='novel' AND workspace_type='novel'
                      AND workspace_id=:w AND resource_type='kb' AND resource_id=:r
                    """
                ),
                {"w": novel_id, "r": kb_id},
            ).fetchone()
            if not exists:
                conn.execute(
                    text(
                        """
                        INSERT INTO resource_mounts
                        (user_id, product_key, workspace_type, workspace_id, resource_type, resource_id, stage)
                        VALUES (:u, 'novel', 'novel', :w, 'kb', :r, NULL)
                        """
                    ),
                    {"u": owner_id, "w": novel_id, "r": kb_id},
                )
    except Exception:
        pass

    # novel skills
    try:
        links = conn.execute(
            text(
                """
                SELECT n.owner_id, nsl.novel_id, nsl.skill_id, nsl.stage
                FROM novel_skill_links nsl
                JOIN novels n ON n.id = nsl.novel_id
                """
            )
        ).fetchall()
        for owner_id, novel_id, skill_id, stage in links:
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM resource_mounts
                    WHERE product_key='novel' AND workspace_type='novel'
                      AND workspace_id=:w AND resource_type='skill' AND resource_id=:r
                      AND (stage=:s OR (stage IS NULL AND :s IS NULL))
                    """
                ),
                {"w": novel_id, "r": skill_id, "s": stage},
            ).fetchone()
            if not exists:
                conn.execute(
                    text(
                        """
                        INSERT INTO resource_mounts
                        (user_id, product_key, workspace_type, workspace_id, resource_type, resource_id, stage)
                        VALUES (:u, 'novel', 'novel', :w, 'skill', :r, :s)
                        """
                    ),
                    {"u": owner_id, "w": novel_id, "r": skill_id, "s": stage},
                )
    except Exception:
        pass


def _backfill_defaults(conn) -> None:
    try:
        conn.execute(text("UPDATE chat_sessions SET product_key='novel' WHERE product_key IS NULL OR product_key=''"))
    except Exception:
        pass
    try:
        conn.execute(text("UPDATE usage_logs SET product_key='novel' WHERE product_key IS NULL AND novel_id IS NOT NULL"))
    except Exception:
        pass
    # SQLite JSON 默认空数组:把 NULL scope_tags 填成 ["shared","novel"]
    for table in ("skills", "knowledge_bases"):
        try:
            conn.execute(
                text(f"UPDATE {table} SET scope_tags='[\"shared\",\"novel\"]' WHERE scope_tags IS NULL OR scope_tags='' OR scope_tags='[]'")
            )
        except Exception:
            pass
