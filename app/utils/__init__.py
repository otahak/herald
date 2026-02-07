"""Utility functions for the Herald application."""

import os
from litestar import Request


def get_base_path(request: Request) -> str:
    """
    Extract the base path from the request (e.g., '/herald' if app is served at /herald).

    Precedence: ASGI root_path > BASE_PATH env > path-based detection > production fallback > "".
    """
    # 1. ASGI root_path (e.g. uvicorn --root-path /herald)
    try:
        scope = getattr(request, "scope", None)
        if scope:
            root_path = scope.get("root_path", "")
            if root_path:
                return root_path.rstrip("/") or ""
    except (AttributeError, KeyError, TypeError):
        pass

    # 2. Explicit BASE_PATH environment variable (no hardcoded /herald)
    base_path_env = os.getenv("BASE_PATH", "").strip().rstrip("/")
    if base_path_env:
        return base_path_env

    # 3. Extract from URL path by known route prefixes
    path = request.url.path
    if "/admin" in path:
        return path[: path.index("/admin")].rstrip("/") or ""
    if "/api" in path:
        return path[: path.index("/api")].rstrip("/") or ""
    for prefix in ["/game", "/feedback", "/users"]:
        if prefix in path:
            return path[: path.index(prefix)].rstrip("/") or ""

    # 4. Root path: production hostname fallback (only when nothing else set)
    if path in ("/", "") and request.url.hostname:
        if "localhost" not in (request.url.hostname or "") and "127.0.0.1" != request.url.hostname:
            return "/herald"

    return ""
