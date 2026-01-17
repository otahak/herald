"""Utility functions for the Herald application."""

from litestar import Request


def get_base_path(request: Request) -> str:
    """
    Extract the base path from the request (e.g., '/herald' if app is served at /herald).
    
    Uses the request's root_path if available (set by uvicorn --root-path option),
    otherwise extracts it from the URL path by finding common route prefixes.
    """
    # First, try to use root_path from ASGI scope (set by uvicorn --root-path)
    # This is the most reliable method when the app is served under a subpath
    try:
        # Access the ASGI scope directly
        scope = getattr(request, "scope", None)
        if scope:
            root_path = scope.get("root_path", "")
            if root_path:
                return root_path
    except (AttributeError, KeyError, TypeError):
        pass
    
    # Fallback: Extract from URL path by looking for known route prefixes
    path = request.url.path
    
    # Check for admin routes
    if "/admin" in path:
        return path[:path.index("/admin")]
    
    # Check for API routes
    if "/api" in path:
        return path[:path.index("/api")]
    
    # Check for other known route prefixes
    known_prefixes = ["/game", "/feedback", "/users"]
    for prefix in known_prefixes:
        if prefix in path:
            return path[:path.index(prefix)]
    
    # If path is just "/" or empty, check if we're on a subpath by looking at the full URL
    if path == "/" or path == "":
        # Check if the hostname suggests we're in production (otahak.com)
        # In production with nginx, the root_path should be set, but as fallback:
        if request.url.hostname and ("otahak.com" in request.url.hostname or "localhost" not in request.url.hostname):
            # Assume /herald base path in production if not explicitly set
            return "/herald"
    
    return ""
