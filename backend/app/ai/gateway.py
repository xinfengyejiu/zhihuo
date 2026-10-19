"""AI 网关:读取用户的用途路由,调用 OpenAI 兼容接口,支持备用模型降级与用量记录。"""

import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..crypto import decrypt_secret
from ..models import AiCallError, AiModelRoute, AiProvider, UsageLog
from ..products import expand_purpose_candidates



@dataclass
class RouteTarget:
    provider: AiProvider
    model_id: str
    temperature: float
    max_tokens: int


class AiNotConfigured(HTTPException):
    def __init__(self, purpose: str):
        super().__init__(
            status_code=400,
            detail=f"AI 用途「{purpose}」尚未配置模型,请到 个人中心 → 模型配置 完成设置",
        )


def _find_route(db: Session, user_id: int, purpose: str) -> AiModelRoute | None:
    for cand in expand_purpose_candidates(purpose):
        route = db.query(AiModelRoute).filter_by(user_id=user_id, purpose=cand).first()
        if route is not None:
            return route
    return None


def resolve_route(db: Session, user_id: int, purpose: str, allow_fallback: bool = False) -> RouteTarget:
    """解析用途路由(兼容 novel.chapter 与旧 chapter);allow_fallback=True 时只取备用。"""
    route = _find_route(db, user_id, purpose)
    if route is None:
        raise AiNotConfigured(purpose)

    if allow_fallback:
        provider_id, model_id = route.fallback_provider_id, route.fallback_model_id
    else:
        provider_id, model_id = route.provider_id, route.model_id

    if not provider_id or not model_id:
        if allow_fallback:
            raise AiNotConfigured(purpose + "(备用)")
        return resolve_route(db, user_id, purpose, allow_fallback=True)

    provider = db.get(AiProvider, provider_id)
    if provider is None or not provider.enabled:
        if allow_fallback:
            raise AiNotConfigured(purpose)
        return resolve_route(db, user_id, purpose, allow_fallback=True)

    return RouteTarget(
        provider=provider,
        model_id=model_id,
        temperature=route.temperature,
        max_tokens=route.max_tokens,
    )


def resolve_route_or_override(
    db: Session,
    user_id: int,
    purpose: str,
    *,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> RouteTarget:
    """请求级覆盖供应商/模型;否则走用途路由。"""
    if provider_id and model_id:
        provider = db.query(AiProvider).filter_by(id=provider_id, user_id=user_id).first()
        if provider is None or not provider.enabled:
            raise HTTPException(400, "指定的供应商不可用")
        base = resolve_route(db, user_id, purpose) if _find_route(db, user_id, purpose) else None
        return RouteTarget(
            provider=provider,
            model_id=model_id,
            temperature=base.temperature if base else 0.8,
            max_tokens=base.max_tokens if base else 4096,
        )
    return resolve_route(db, user_id, purpose)


async def stream_chat(
    db: Session,
    user_id: int,
    novel_id: int | None,
    purpose: str,
    messages: list[dict],
    temperature_override: float | None = None,
    max_tokens_override: int | None = None,
    allow_reasoning_only: bool = False,
    provider_id: int | None = None,
    model_id: str | None = None,
    product_key: str | None = None,
    workspace_id: int | None = None,
) -> AsyncIterator[dict]:
    """流式调用。产出 {"type":"delta","content":...} / {"type":"usage",...} / {"type":"model",...}。

    主模型失败自动降级到备用模型;全程记录用量。
    allow_reasoning_only=True 时(如 AI 审查),思考模型只吐 reasoning 也视为成功并回填为正文。
    """
    tried: list[str] = []
    wid = workspace_id if workspace_id is not None else novel_id
    try:
        target = resolve_route_or_override(
            db, user_id, purpose, provider_id=provider_id, model_id=model_id
        )
    except HTTPException as e:
        log_call_error(
            db,
            user_id,
            purpose,
            str(getattr(e, "detail", e)),
            novel_id=novel_id,
            product_key=product_key,
            workspace_id=wid,
            status="failed",
        )
        raise
    if temperature_override is not None:
        target.temperature = temperature_override
    if max_tokens_override is not None:
        target.max_tokens = max_tokens_override
    try:
        async for event in _stream_one(target, messages, tried, allow_reasoning_only=allow_reasoning_only):
            if event.get("type") == "usage":
                event = {
                    **event,
                    "_product_key": product_key,
                    "_workspace_id": wid,
                }
            yield event
        return
    except _AllFailed:
        pass

    primary_err = ";".join(tried) or "主模型调用失败"

    # 主模型失败 → 尝试备用(有覆盖时仍尝试用途备用)
    try:
        fallback = resolve_route(db, user_id, purpose, allow_fallback=True)
    except HTTPException:
        log_call_error(
            db,
            user_id,
            purpose,
            primary_err,
            novel_id=novel_id,
            product_key=product_key,
            workspace_id=wid,
            provider_name=target.provider.name,
            model_name=target.model_id,
            status="failed",
        )
        raise _to_http(tried)

    log_call_error(
        db,
        user_id,
        purpose,
        primary_err,
        novel_id=novel_id,
        product_key=product_key,
        workspace_id=wid,
        provider_name=target.provider.name,
        model_name=target.model_id,
        status="degraded",
    )
    if max_tokens_override is not None:
        fallback.max_tokens = max_tokens_override
    try:
        async for event in _stream_one(fallback, messages, tried, allow_reasoning_only=allow_reasoning_only):
            yield event
        return
    except _AllFailed:
        pass
    log_call_error(
        db,
        user_id,
        purpose,
        ";".join(tried) if tried else "未知的供应商错误",
        novel_id=novel_id,
        product_key=product_key,
        workspace_id=wid,
        provider_name=fallback.provider.name,
        model_name=fallback.model_id,
        status="failed",
    )
    raise _to_http(tried)


def normalize_provider_base(base_url: str) -> str:
    """原样使用用户配置的 Base URL,只去掉首尾空白与末尾斜杠,绝不自动加 /v1。"""
    return (base_url or "").strip().rstrip("/")


def join_provider_url(base_url: str, path: str) -> str:
    """拼接接口路径。若用户已把完整地址填进 Base URL(以该 path 结尾),则不再追加。"""
    base = normalize_provider_base(base_url)
    suffix = "/" + (path or "").strip("/")
    if not base:
        return suffix
    if base.lower().endswith(suffix.lower()):
        return base
    return f"{base}{suffix}"


def _provider_base(provider: AiProvider) -> str:
    return normalize_provider_base(provider.base_url)


def resolve_any_purpose(db: Session, user_id: int, candidates: list[str]) -> str:
    """按优先级找到第一个已配置的用途;都没有则给出明确提示。"""
    for purpose in candidates:
        try:
            resolve_route(db, user_id, purpose)
            return purpose
        except HTTPException:
            continue
    raise HTTPException(400, f"请先在 个人中心 → AI 供应商配置 里配置以下任一用途:{'、'.join(candidates)}")


async def embed_texts(db: Session, user_id: int, texts: list[str]) -> list[list[float]]:
    """调用用户配置的 Embedding 模型,返回向量列表。"""
    try:
        target = resolve_route(db, user_id, "embedding")
    except HTTPException as e:
        log_call_error(db, user_id, "embedding", str(getattr(e, "detail", e)), product_key="shared", status="failed")
        raise
    url = join_provider_url(target.provider.base_url, "embeddings")
    headers = {"Authorization": f"Bearer {decrypt_secret(target.provider.api_key_encrypted)}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
            resp = await client.post(url, headers=headers, json={"model": target.model_id, "input": texts})
    except httpx.HTTPError as e:
        detail = f"Embedding 调用失败:{e.__class__.__name__}"
        log_call_error(
            db,
            user_id,
            "embedding",
            detail,
            product_key="shared",
            provider_name=target.provider.name,
            model_name=target.model_id,
            status="failed",
        )
        raise HTTPException(502, detail)
    if resp.status_code != 200:
        detail = f"Embedding 调用失败 HTTP {resp.status_code}: {resp.text[:200]}"
        log_call_error(
            db,
            user_id,
            "embedding",
            detail,
            product_key="shared",
            provider_name=target.provider.name,
            model_name=target.model_id,
            status="failed",
        )
        raise HTTPException(502, detail)
    try:
        data = resp.json()
        vectors = [item["embedding"] for item in data["data"]]
        usage = data.get("usage") or {}
    except (ValueError, KeyError, TypeError):
        detail = "Embedding 响应格式异常"
        log_call_error(
            db,
            user_id,
            "embedding",
            detail,
            product_key="shared",
            provider_name=target.provider.name,
            model_name=target.model_id,
            status="failed",
        )
        raise HTTPException(502, detail)
    log_usage(
        db,
        user_id,
        None,
        "embedding",
        {
            "provider_name": target.provider.name,
            "model_name": target.model_id,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": 0,
            "_product_key": "shared",
        },
    )
    return vectors


class _AllFailed(Exception):
    pass


def _to_http(tried: list[str]):
    detail = "AI 调用失败:" + (";".join(tried) if tried else "未知的供应商错误")
    # 常见网关故障给更可操作的提示
    joined = " ".join(tried)
    if "503" in joined or "no_available_providers" in joined or "暂时不可用" in joined:
        detail += "。当前主模型上游无可用节点,请到个人中心把「章节生成」换成其它模型,或稍后再试"
    return HTTPException(status_code=502, detail=detail)


def _clamp_max_tokens(n: int | float | None, *, hard_cap: int = 65536) -> int:
    """多数供应商(含 Agnes)单次 max_tokens ≤ 65536;路由里误填 128000 会直接 400。"""
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        v = 4096
    return min(v, hard_cap)


async def _stream_one(
    target: RouteTarget,
    messages: list[dict],
    tried: list[str],
    allow_reasoning_only: bool = False,
) -> AsyncIterator[dict]:
    url = join_provider_url(target.provider.base_url, "chat/completions")
    headers = {"Authorization": f"Bearer {decrypt_secret(target.provider.api_key_encrypted)}"}
    max_tokens = _clamp_max_tokens(target.max_tokens)
    payloads = [
        {
            "model": target.model_id,
            "messages": messages,
            "temperature": target.temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        # 部分供应商不认 stream_options
        {
            "model": target.model_id,
            "messages": messages,
            "temperature": target.temperature,
            "max_tokens": max_tokens,
            "stream": True,
        },
        # 仍因 max_tokens 被拒时再降到 8192 重试
        {
            "model": target.model_id,
            "messages": messages,
            "temperature": target.temperature,
            "max_tokens": min(max_tokens, 8192),
            "stream": True,
        },
    ]
    label = f"{target.provider.name}/{target.model_id}"
    usage: dict | None = None
    got_content = False
    got_reasoning = False
    reasoning_buf: list[str] = []
    last_err = ""

    try:
        # read 超时是两次数据之间的间隔;推理模型首包可能较慢
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            opened = False
            for payload in payloads:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        last_err = (await resp.aread()).decode(errors="replace")[:300]
                        if resp.status_code == 429:
                            tried.append(
                                f"{label}: 速率限额(429)。免费额度用尽时需稍后再试或升级套餐。详情:{last_err[:160]}"
                            )
                            raise _AllFailed()
                        # 400/422:换下一套 payload(去 stream_options / 降 max_tokens);其它状态交上层降级
                        if resp.status_code in (400, 422) and payload is not payloads[-1]:
                            continue
                        tried.append(f"{label}: HTTP {resp.status_code} {last_err}")
                        raise _AllFailed()
                    opened = True
                    tried.append(label)
                    yield {"type": "model", "model": label}
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                got_content = True
                                yield {"type": "delta", "content": piece}
                            # 思考型模型(deepseek-r1 风格):思考过程增量转发
                            reasoning = (
                                delta.get("reasoning_content")
                                or delta.get("reasoning")
                                or (choice.get("message") or {}).get("reasoning_content")
                            )
                            if reasoning:
                                got_reasoning = True
                                reasoning_buf.append(reasoning)
                                yield {"type": "reasoning", "content": reasoning}
                    break
            if not opened:
                tried.append(f"{label}: HTTP 失败 {last_err}")
                raise _AllFailed()
    except httpx.HTTPError:
        tried.append(f"{label}: 网络错误/超时")
        raise _AllFailed()

    if not got_content:
        if got_reasoning and allow_reasoning_only:
            salvaged = "".join(reasoning_buf).strip()
            if salvaged:
                yield {"type": "notice", "detail": "模型主要输出了思考过程,已回填为正文(建议改用非推理模型以获得更稳的正文)"}
                yield {"type": "delta", "content": salvaged}
                got_content = True
        if not got_content:
            if got_reasoning:
                tried.append(f"{label}: 模型只返回了思考过程,没有正文。请换非推理模型,或把该用途的 max_tokens 调大")
            else:
                tried.append(f"{label}: 模型未返回任何正文")
            raise _AllFailed()

    if usage:
        yield {
            "type": "usage",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "provider_name": target.provider.name,
            "model_name": target.model_id,
        }
    else:
        yield {
            "type": "usage",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "provider_name": target.provider.name,
            "model_name": target.model_id,
        }


def log_usage(
    db: Session,
    user_id: int,
    novel_id: int | None,
    purpose: str,
    usage: dict,
    *,
    product_key: str | None = None,
    workspace_id: int | None = None,
):
    pk = product_key or usage.get("_product_key")
    if not pk:
        pk = "novel" if novel_id else None
    wid = workspace_id if workspace_id is not None else usage.get("_workspace_id")
    if wid is None:
        wid = novel_id
    db.add(
        UsageLog(
            user_id=user_id,
            novel_id=novel_id,
            product_key=pk,
            workspace_id=wid,
            purpose=purpose,
            provider_name=usage.get("provider_name"),
            model_name=usage.get("model_name"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
    )
    db.commit()


def log_call_error(
    db: Session,
    user_id: int,
    purpose: str,
    error: str,
    *,
    novel_id: int | None = None,
    product_key: str | None = None,
    workspace_id: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    status: str = "failed",
):
    """记录 AI 调用失败或主模型失败后降级。写库失败不阻断主流程。"""
    try:
        pk = product_key
        if not pk:
            pk = "novel" if novel_id else None
        db.add(
            AiCallError(
                user_id=user_id,
                novel_id=novel_id,
                product_key=pk,
                workspace_id=workspace_id if workspace_id is not None else novel_id,
                purpose=purpose,
                provider_name=provider_name,
                model_name=model_name,
                status=status if status in ("failed", "degraded") else "failed",
                error=(error or "")[:2000],
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
