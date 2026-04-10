from __future__ import annotations

from typing import Any


def success(data: Any = None, message: str = "success", **extra: Any) -> dict[str, Any]:
    payload = {"code": 0, "message": message, "data": data}
    payload.update(extra)
    return payload


def error(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "data": None}
