from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine
from .schema_patch import ensure_schema
from .routers import (
    auth,
    chapters,
    chat,
    collab,
    drama,
    export,
    gen_assets,
    hub,
    insights,
    kb,
    media,
    mounts,
    novels,
    providers,
    publish,
    reader,
    reader_store,
    review,
    settings as settings_router,
    skills,
    usage,
    visual,
)

app = FastAPI(title="织火创作平台", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(auth.profile_router, prefix=API_PREFIX)
app.include_router(hub.router, prefix=API_PREFIX)
app.include_router(providers.router, prefix=API_PREFIX)
app.include_router(providers.routes_router, prefix=API_PREFIX)
app.include_router(novels.router, prefix=API_PREFIX)
app.include_router(novels.tags_router, prefix=API_PREFIX)
app.include_router(settings_router.router, prefix=API_PREFIX)
app.include_router(chapters.router, prefix=API_PREFIX)
app.include_router(review.router, prefix=API_PREFIX)
app.include_router(reader.router, prefix=API_PREFIX)
app.include_router(reader_store.router, prefix=API_PREFIX)
app.include_router(publish.router, prefix=API_PREFIX)
app.include_router(kb.router, prefix=API_PREFIX)
app.include_router(kb.mount_router, prefix=API_PREFIX)
app.include_router(skills.router, prefix=API_PREFIX)
app.include_router(skills.mount_router, prefix=API_PREFIX)
app.include_router(mounts.router, prefix=API_PREFIX)
app.include_router(media.router, prefix=API_PREFIX)
app.include_router(drama.router, prefix=API_PREFIX)
app.include_router(gen_assets.router, prefix=API_PREFIX)
app.include_router(insights.router, prefix=API_PREFIX)
app.include_router(export.router, prefix=API_PREFIX)
app.include_router(collab.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(usage.router, prefix=API_PREFIX)
app.include_router(visual.router, prefix=API_PREFIX)


@app.on_event("startup")
def on_startup():
    # M1 用 create_all 建表;增量列用 schema_patch 补齐
    Base.metadata.create_all(bind=engine)
    ensure_schema()


@app.get("/api/health")
def health():
    return {"ok": True}
