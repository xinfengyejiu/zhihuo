"""AI 结构化提取:markdown/自由文本 → JSON(数组或对象),用于设定库批量导入与对话选题建书。"""

import asyncio
import json
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .gateway import log_usage, resolve_any_purpose, stream_chat


def _resolve_extraction_purpose(db: Session, user_id: int) -> str:
    """结构化提取优先用「复盘分析」模型,未配置时按顺序兜底到其他已配置用途。"""
    return resolve_any_purpose(db, user_id, ["analysis", "outline", "chat", "chapter", "polish"])


async def _collect(db: Session, user_id: int, purpose: str, messages: list[dict], max_tokens_override: int | None = None):
    parts: list[str] = []
    usage: dict = {}
    async for event in stream_chat(db, user_id, None, purpose, messages, max_tokens_override=max_tokens_override):
        if event["type"] == "delta":
            parts.append(event["content"])
        elif event["type"] == "usage":
            usage = event
    return "".join(parts), usage


def _call_llm_json_text(db: Session, user_id: int, system_prompt: str, user_text: str, novel_id: int | None) -> str:
    purpose = _resolve_extraction_purpose(db, user_id)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text[:12000]},
    ]
    loop = asyncio.new_event_loop()
    try:
        # 结构化提取输出较大,给足 max_tokens
        text, usage = loop.run_until_complete(
            _collect(db, user_id, purpose, messages, max_tokens_override=8000)
        )
    finally:
        loop.close()
    log_usage(db, user_id, novel_id, purpose, usage)

    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return text


def _loads_lenient(raw: str):
    return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))  # 容错:去掉尾逗号


def extract_json_array(db: Session, user_id: int, system_prompt: str, user_text: str, novel_id: int | None = None) -> list:
    text = _call_llm_json_text(db, user_id, system_prompt, user_text, novel_id)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        # 有些模型会包一层对象再给数组,如 {"items":[...]}
        obj_start, obj_end = text.find("{"), text.rfind("}")
        if obj_start != -1 and obj_end > obj_start:
            text = text[obj_start : obj_end + 1]
            start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise HTTPException(502, "AI 未返回有效的 JSON 数组,请重试一次或把文本整理得更结构化")
    try:
        data = _loads_lenient(text[start : end + 1])
    except json.JSONDecodeError:
        raise HTTPException(502, "AI 返回的 JSON 无法解析,请重试一次")
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        raise HTTPException(502, "AI 返回的不是数组,请重试一次")
    return [item for item in data if isinstance(item, dict)]


def extract_json_object(db: Session, user_id: int, system_prompt: str, user_text: str, novel_id: int | None = None) -> dict:
    text = _call_llm_json_text(db, user_id, system_prompt, user_text, novel_id)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise HTTPException(502, "AI 未返回有效的 JSON 对象,请重试一次")
    try:
        data = _loads_lenient(text[start : end + 1])
    except json.JSONDecodeError:
        raise HTTPException(502, "AI 返回的 JSON 无法解析,请重试一次")
    if not isinstance(data, dict):
        raise HTTPException(502, "AI 返回的不是对象,请重试一次")
    return data
