import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai.gateway import join_provider_url
from ..ai.modality import infer_modality
from ..crypto import decrypt_secret, encrypt_secret, mask_secret
from ..database import get_db
from ..models import AI_PURPOSE_LABELS, AI_PURPOSES, AiModel, AiModelRoute, AiProvider, User
from ..products import PRODUCTS, list_products, purpose_key
from ..schemas import ModelToggleIn, ProviderIn, ProviderOut, ProviderUpdateIn, RouteIn, RouteOut
from ..security import get_current_user

router = APIRouter(prefix="/providers", tags=["providers"])


def _all_purposes() -> list[str]:
    """产品化用途键 + 旧短名(兼容)。"""
    keys: list[str] = []
    for p in list_products(only_enabled=False):
        for short in p.purposes:
            keys.append(purpose_key(p.key, short))
    keys.append("shared.embedding")
    # 旧短名仍可读写,便于迁移期
    for short in AI_PURPOSES:
        if short not in keys:
            keys.append(short)
    return keys


def _out(p: AiProvider) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        name=p.name,
        base_url=p.base_url,
        api_key_masked=mask_secret(decrypt_secret(p.api_key_encrypted)),
        enabled=p.enabled,
        remark=p.remark,
    )


@router.get("", response_model=list[ProviderOut])
def list_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(AiProvider).filter_by(user_id=user.id).order_by(AiProvider.id).all()
    return [_out(p) for p in rows]


@router.post("", response_model=ProviderOut)
def create_provider(body: ProviderIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = AiProvider(
        user_id=user.id,
        name=body.name,
        base_url=body.base_url.rstrip("/"),
        api_key_encrypted=encrypt_secret(body.api_key),
        enabled=body.enabled,
        remark=body.remark,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(p)


@router.put("/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int,
    body: ProviderUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    if body.name is not None:
        p.name = body.name
    if body.base_url is not None:
        p.base_url = body.base_url.rstrip("/")
    if body.api_key:
        p.api_key_encrypted = encrypt_secret(body.api_key)
    if body.enabled is not None:
        p.enabled = body.enabled
    if body.remark is not None:
        p.remark = body.remark
    db.commit()
    db.refresh(p)
    return _out(p)


@router.delete("/{provider_id}")
def delete_provider(provider_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    db.delete(p)
    db.commit()
    return {"ok": True}


def _is_embedding_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return "embed" in mid or "bge-" in mid or mid.startswith("text-embedding")


def _list_remote_models(p: AiProvider) -> dict:
    """拉取供应商 /models 列表(供同步用)。"""
    headers = {"Authorization": f"Bearer {decrypt_secret(p.api_key_encrypted)}"}
    try:
        resp = httpx.get(join_provider_url(p.base_url, "models"), headers=headers, timeout=15)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"网络错误:{e.__class__.__name__}", "models": []}
    if resp.status_code != 200:
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}", "models": []}
    try:
        data = resp.json()
        model_ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    except ValueError:
        return {"ok": False, "error": "响应不是合法 JSON", "models": []}
    return {"ok": True, "error": None, "models": model_ids}


def _probe_one_model(base_url: str, headers: dict, model_id: str, modality: str) -> dict:
    """对单个已入库模型做最小调用探测。Base URL 原样使用,已是完整路径则不再拼接。"""
    mid = model_id
    timeout = httpx.Timeout(45, connect=10)
    try:
        with httpx.Client(timeout=timeout) as client:
            if _is_embedding_model(mid) or modality == "embedding":
                resp = client.post(
                    join_provider_url(base_url, "embeddings"),
                    headers=headers,
                    json={"model": mid, "input": "ping"},
                )
            elif modality == "image":
                resp = client.post(
                    join_provider_url(base_url, "images/generations"),
                    headers=headers,
                    json={"model": mid, "prompt": "a red dot", "n": 1, "size": "256x256"},
                )
            elif modality == "video":
                # 视频生成昂贵且接口不一:优先验证模型在远端列表,否则试 videos/generations
                try:
                    listed = client.get(join_provider_url(base_url, "models"), headers=headers, timeout=10)
                    if listed.status_code == 200:
                        ids = {
                            m.get("id")
                            for m in (listed.json().get("data") or [])
                            if isinstance(m, dict) and m.get("id")
                        }
                        if mid in ids:
                            return {
                                "model_id": mid,
                                "modality": modality,
                                "ok": True,
                                "error": None,
                                "detail": "远端模型列表可见(未实际生成视频)",
                            }
                except Exception:
                    pass
                resp = client.post(
                    join_provider_url(base_url, "videos/generations"),
                    headers=headers,
                    json={"model": mid, "prompt": "a red ball rolling", "n": 1},
                )
            else:
                resp = client.post(
                    join_provider_url(base_url, "chat/completions"),
                    headers=headers,
                    json={
                        "model": mid,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                )
    except httpx.HTTPError as e:
        return {
            "model_id": mid,
            "modality": modality,
            "ok": False,
            "error": f"网络错误/超时:{e.__class__.__name__}",
            "detail": None,
        }

    if resp.status_code == 200:
        return {"model_id": mid, "modality": modality, "ok": True, "error": None, "detail": None}
    # 部分供应商对 max_tokens=1 / 小图不支持,但说明鉴权与模型路由已通
    body = (resp.text or "")[:240]
    soft_ok = resp.status_code in (400, 422) and any(
        k in body.lower() for k in ("max_tokens", "max tokens", "size", "parameter", "invalid")
    )
    if soft_ok:
        return {
            "model_id": mid,
            "modality": modality,
            "ok": True,
            "error": None,
            "detail": f"模型可达(参数被拒 HTTP {resp.status_code})",
        }
    return {
        "model_id": mid,
        "modality": modality,
        "ok": False,
        "error": f"HTTP {resp.status_code}: {body}",
        "detail": None,
    }


@router.post("/{provider_id}/test")
def test_provider(provider_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """测试本供应商已入库的全部模型(真实最小调用)。"""
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    rows = (
        db.query(AiModel)
        .filter_by(provider_id=provider_id)
        .order_by(AiModel.model_id)
        .all()
    )
    if not rows:
        return {
            "ok": False,
            "error": "尚无已入库模型,请先「同步模型」或在编辑里手动添加",
            "models": [],
            "results": [],
            "passed": 0,
            "failed": 0,
        }

    headers = {"Authorization": f"Bearer {decrypt_secret(p.api_key_encrypted)}"}
    results = []
    for m in rows:
        mod = infer_modality(m.model_id, m.modality)
        if _is_embedding_model(m.model_id):
            mod = "embedding"
        results.append(_probe_one_model(p.base_url, headers, m.model_id, mod))

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    failed_names = [r["model_id"] for r in results if not r["ok"]]
    ok = failed == 0
    error = None if ok else f"{failed}/{len(results)} 个模型失败: {', '.join(failed_names[:5])}"
    return {
        "ok": ok,
        "error": error,
        "models": [r["model_id"] for r in results if r["ok"]],
        "results": results,
        "passed": passed,
        "failed": failed,
    }


@router.post("/{provider_id}/sync-models")
def sync_models(provider_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """拉取模型列表并入库(全部标记启用)。"""
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    result = _list_remote_models(p)
    if not result["ok"]:
        raise HTTPException(400, result["error"] or "拉取失败")
    existing = {m.model_id: m for m in db.query(AiModel).filter_by(provider_id=provider_id).all()}
    for mid in result["models"]:
        if mid not in existing:
            db.add(
                AiModel(
                    provider_id=provider_id,
                    model_id=mid,
                    enabled=True,
                    modality=infer_modality(mid),
                )
            )
        else:
            row = existing[mid]
            row.enabled = True
            if not row.modality:
                row.modality = infer_modality(mid)
    db.commit()
    return {"ok": True, "count": len(result["models"])}


@router.get("/{provider_id}/models", response_model=list)
def list_models(provider_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    rows = db.query(AiModel).filter_by(provider_id=provider_id).order_by(AiModel.model_id).all()
    out = []
    for m in rows:
        mod = infer_modality(m.model_id, m.modality)
        out.append(
            {
                "id": m.id,
                "model_id": m.model_id,
                "display_name": m.display_name,
                "enabled": m.enabled,
                "modality": mod,
            }
        )
    return out


@router.put("/{provider_id}/models")
def toggle_model(
    provider_id: int,
    body: ModelToggleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(AiProvider).filter_by(id=provider_id, user_id=user.id).first()
    if p is None:
        raise HTTPException(404, "供应商不存在")
    row = db.query(AiModel).filter_by(provider_id=provider_id, model_id=body.model_id).first()
    if row is None:
        row = AiModel(provider_id=provider_id, model_id=body.model_id, display_name=body.display_name)
        db.add(row)
    row.enabled = body.enabled
    if body.display_name:
        row.display_name = body.display_name
    if body.modality is not None:
        if body.modality not in ("text", "image", "video", ""):
            raise HTTPException(400, "modality 须为 text / image / video")
        row.modality = body.modality or None
    if not row.modality:
        row.modality = infer_modality(row.model_id)
    db.commit()
    return {"ok": True, "modality": infer_modality(row.model_id, row.modality)}


# ---------- 用途路由 ----------
routes_router = APIRouter(prefix="/routes", tags=["providers"])


@routes_router.get("/meta")
def routes_meta():
    """产品 × 用途清单,供个人中心分组展示。"""
    products = []
    for p in list_products(only_enabled=False):
        products.append(
            {
                "key": p.key,
                "name": p.name,
                "purposes": [
                    {
                        "key": purpose_key(p.key, short),
                        "short": short,
                        "label": AI_PURPOSE_LABELS.get(short, short),
                    }
                    for short in p.purposes
                ],
            }
        )
    products.append(
        {
            "key": "shared",
            "name": "共享",
            "purposes": [{"key": "shared.embedding", "short": "embedding", "label": "向量记忆"}],
        }
    )
    return {"products": products}


@routes_router.get("", response_model=list[RouteOut])
def get_routes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = {r.purpose: r for r in db.query(AiModelRoute).filter_by(user_id=user.id).all()}
    out = []
    for purpose in _all_purposes():
        r = rows.get(purpose)
        # 若只有旧短名配置,也回填到对应 novel.* 展示
        if r is None and "." in purpose:
            short = purpose.split(".", 1)[1]
            r = rows.get(short)
        out.append(
            RouteOut(
                purpose=purpose,
                provider_id=r.provider_id if r else None,
                model_id=r.model_id if r else None,
                temperature=r.temperature if r else 0.8,
                max_tokens=r.max_tokens if r else 4096,
                fallback_provider_id=r.fallback_provider_id if r else None,
                fallback_model_id=r.fallback_model_id if r else None,
            )
        )
    return out


@routes_router.put("/{purpose:path}", response_model=RouteOut)
def put_route(
    purpose: str,
    body: RouteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed = set(_all_purposes())
    if purpose not in allowed:
        raise HTTPException(400, f"未知用途:{purpose}")
    row = db.query(AiModelRoute).filter_by(user_id=user.id, purpose=purpose).first()
    if row is None:
        row = AiModelRoute(user_id=user.id, purpose=purpose)
        db.add(row)
    row.provider_id = body.provider_id
    row.model_id = body.model_id
    row.temperature = body.temperature
    row.max_tokens = body.max_tokens
    row.fallback_provider_id = body.fallback_provider_id
    row.fallback_model_id = body.fallback_model_id
    db.commit()
    db.refresh(row)
    return RouteOut(
        purpose=purpose,
        provider_id=row.provider_id,
        model_id=row.model_id,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        fallback_provider_id=row.fallback_provider_id,
        fallback_model_id=row.fallback_model_id,
    )
