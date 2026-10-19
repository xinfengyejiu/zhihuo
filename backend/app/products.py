"""平台产品注册表:Hub 卡片与用途清单的单一来源。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductDef:
    key: str
    name: str
    description: str
    href: str
    purposes: tuple[str, ...]  # 不含 product 前缀的短名
    enabled: bool = True


PRODUCTS: dict[str, ProductDef] = {
    "novel": ProductDef(
        key="novel",
        name="小说创作",
        description="长篇网文：书架、大纲章节、设定库、审核与发布",
        href="/shelf",
        purposes=("chat", "outline", "chapter", "polish", "analysis", "summary", "embedding", "visual"),
    ),
    "media": ProductDef(
        key="media",
        name="自媒体写作",
        description="小红书 / 公众号 / 头条等：选题、聊天式创作与成稿",
        href="/media/chat",
        purposes=("chat", "topic_expand", "polish", "embedding"),
    ),
    "drama": ProductDef(
        key="drama",
        name="漫剧创作",
        description="聊天改剧、粘贴剧本 → 分镜时间轴与首帧",
        href="/drama/chat",
        purposes=("chat", "script", "visual"),
    ),
}


def list_products(*, only_enabled: bool = True) -> list[ProductDef]:
    items = list(PRODUCTS.values())
    if only_enabled:
        items = [p for p in items if p.enabled]
    return items


def purpose_key(product: str, purpose: str) -> str:
    if "." in purpose:
        return purpose
    return f"{product}.{purpose}"


def expand_purpose_candidates(purpose: str) -> list[str]:
    """兼容旧短名与新 product.purpose。"""
    seen: list[str] = []
    for p in (purpose,):
        if p not in seen:
            seen.append(p)
    if "." in purpose:
        short = purpose.split(".", 1)[1]
        if short not in seen:
            seen.append(short)
    else:
        for prod in PRODUCTS:
            full = f"{prod}.{purpose}"
            if full not in seen:
                seen.append(full)
        # 共享 embedding
        if purpose == "embedding" and "shared.embedding" not in seen:
            seen.append("shared.embedding")
    return seen
