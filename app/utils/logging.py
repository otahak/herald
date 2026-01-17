"""Enhanced logging utilities with conditional debug logging."""

import logging
import traceback
from os import getenv
from typing import Optional, Any

# Check if debug mode is enabled
DEBUG = getenv("APP_DEBUG", "false").lower() == "true"

# Get the main logger
logger = logging.getLogger("Herald")


def debug_log(message: str, *args, **kwargs) -> None:
    """
    Log a debug message only if APP_DEBUG is enabled.
    
    Args:
        message: Log message (supports % formatting)
        *args: Positional arguments for message formatting
        **kwargs: Keyword arguments (level, exc_info, etc.)
    """
    if DEBUG:
        level = kwargs.pop("level", logging.DEBUG)
        logger.log(level, message, *args, **kwargs)


def error_log(
    message: str,
    exc: Optional[Exception] = None,
    context: Optional[dict] = None,
    *args,
    **kwargs
) -> None:
    """
    Enhanced error logging with context and traceback.
    
    Args:
        message: Error message
        exc: Optional exception object
        context: Optional dictionary with additional context (request info, user, etc.)
        *args: Additional positional arguments
        **kwargs: Additional keyword arguments for logger
    """
    # Build enhanced message
    parts = [message]
    
    if context:
        context_str = ", ".join(f"{k}={v}" for k, v in context.items())
        parts.append(f"Context: {context_str}")
    
    if exc:
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        parts.append(f"Exception: {exc_type}: {exc_msg}")
        
        # Include full traceback in debug mode
        if DEBUG:
            parts.append(f"Traceback:\n{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")
    
    full_message = " | ".join(parts)
    
    # Log with exception info if exception provided
    if exc:
        logger.error(full_message, exc_info=exc, *args, **kwargs)
    else:
        logger.error(full_message, *args, **kwargs)


def log_exception_with_context(
    exc: Exception,
    context: Optional[dict] = None,
    message: Optional[str] = None
) -> None:
    """
    Log an exception with enhanced context information.
    
    Args:
        exc: The exception to log
        context: Optional dictionary with context (request path, user, params, etc.)
        message: Optional custom message
    """
    msg = message or f"Unhandled exception: {type(exc).__name__}"
    error_log(msg, exc=exc, context=context)


def log_request_error(
    request: Any,
    exc: Exception,
    message: Optional[str] = None
) -> None:
    """
    Log an error with request context.
    
    Args:
        request: Request object (should have url, method, etc.)
        exc: The exception
        message: Optional custom message
    """
    context = {}
    
    try:
        if hasattr(request, "url"):
            context["path"] = str(request.url.path) if hasattr(request.url, "path") else str(request.url)
        if hasattr(request, "method"):
            context["method"] = request.method
        if hasattr(request, "headers"):
            context["user_agent"] = request.headers.get("user-agent", "unknown")
    except Exception:
        pass  # Don't fail if we can't extract context
    
    log_exception_with_context(exc, context=context, message=message)
