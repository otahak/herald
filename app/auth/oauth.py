"""Google OAuth authentication."""

import logging
import secrets
from os import getenv
from urllib.parse import urlencode

from authlib.integrations.httpx_client import AsyncOAuth2Client
from litestar import Request
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers.base import BaseRouteHandler
from litestar.response import Redirect, Response
from litestar.stores.memory import MemoryStore

logger = logging.getLogger("Herald.auth")

# OAuth configuration
GOOGLE_CLIENT_ID = getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTHORIZED_EMAIL = getenv("GOOGLE_AUTHORIZED_EMAIL", "otahak@gmail.com")
ADMIN_SESSION_KEY = "admin_authenticated"
ADMIN_EMAIL_KEY = "admin_email"

# OAuth endpoints
GOOGLE_AUTHORIZATION_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Session store (in-memory for now, can be upgraded to Redis later)
session_store = MemoryStore()


def get_base_path(request: Request) -> str:
    """Extract the base path from the request (e.g., '/herald' if app is served at /herald)."""
    path = request.url.path
    # Find the base path by looking for /admin in the path
    if "/admin" in path:
        return path[:path.index("/admin")]
    return ""


def get_redirect_uri(request: Request) -> str:
    """Get the OAuth redirect URI based on the request."""
    scheme = request.url.scheme
    host = request.url.hostname
    port = request.url.port
    base_path = get_base_path(request)
    
    # Determine if we're in production or local
    if host == "otahak.com" or host.endswith(".otahak.com"):
        return f"{scheme}://{host}{base_path}/admin/callback"
    else:
        # Local development
        if port and port != (443 if scheme == "https" else 80):
            return f"{scheme}://{host}:{port}{base_path}/admin/callback"
        return f"{scheme}://{host}{base_path}/admin/callback"


async def get_oauth_client(request: Request) -> AsyncOAuth2Client:
    """Create an OAuth2 client for the current request."""
    redirect_uri = get_redirect_uri(request)
    
    return AsyncOAuth2Client(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        redirect_uri=redirect_uri,
    )


async def admin_login(request: Request) -> Redirect:
    """Initiate Google OAuth login."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.error("Google OAuth credentials not configured")
        raise NotAuthorizedException("OAuth not configured")
    
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    
    # Store state in session
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = secrets.token_urlsafe(32)
    
    await session_store.set(f"oauth_state:{session_id}", state, expires_in=600)  # 10 min expiry
    
    # Build authorization URL
    client = await get_oauth_client(request)
    auth_url, _ = client.create_authorization_url(
        GOOGLE_AUTHORIZATION_BASE_URL,
        state=state,
        scope="openid email profile",
    )
    
    response = Redirect(auth_url)
    # Always set the cookie to ensure it's sent back
    # Use secure=False for localhost, secure=True for production
    is_secure = request.url.scheme == "https" or (request.url.hostname and "otahak.com" in request.url.hostname)
    response.set_cookie(
        "session_id", 
        session_id, 
        httponly=True, 
        secure=is_secure, 
        samesite="lax",
        path="/"  # Make sure cookie is available for the entire site
    )
    
    logger.debug(f"Set session_id cookie: {session_id[:8]}... for redirect to Google")
    
    return response


async def admin_callback(request: Request) -> Redirect | Response:
    """Handle Google OAuth callback."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    
    if error:
        logger.warning(f"OAuth error: {error}")
        return Response(
            content={"error": "Authentication failed"},
            status_code=400,
            media_type="application/json"
        )
    
    if not code or not state:
        return Response(
            content={"error": "Missing code or state"},
            status_code=400,
            media_type="application/json"
        )
    
    # Verify state token
    session_id = request.cookies.get("session_id")
    if not session_id:
        logger.warning(f"No session_id cookie found in callback. Cookies: {list(request.cookies.keys())}")
        return Response(
            content={"error": "No session found"},
            status_code=400,
            media_type="application/json"
        )
    
    logger.debug(f"Looking up state for session_id: {session_id[:8]}...")
    stored_state_raw = await session_store.get(f"oauth_state:{session_id}")
    
    if not stored_state_raw:
        logger.warning(f"No stored state found for session_id: {session_id[:8]}...")
        return Response(
            content={"error": "State token expired or not found"},
            status_code=400,
            media_type="application/json"
        )
    
    # Handle bytes vs string (MemoryStore may return bytes)
    if isinstance(stored_state_raw, bytes):
        stored_state = stored_state_raw.decode('utf-8')
    else:
        stored_state = str(stored_state_raw)
    
    if stored_state != state:
        logger.warning(f"OAuth state mismatch. Stored type: {type(stored_state_raw)}, Stored: {stored_state[:8] if len(stored_state) > 8 else stored_state}..., Received: {state[:8] if len(state) > 8 else state}...")
        return Response(
            content={"error": "Invalid state token"},
            status_code=400,
            media_type="application/json"
        )
    
    logger.debug("State token verified successfully")
    
    # Exchange code for token
    try:
        redirect_uri = get_redirect_uri(request)
        
        # Create client and use it in a context manager
        async with AsyncOAuth2Client(
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            redirect_uri=redirect_uri,
        ) as client:
            # Exchange code for token
            token_response = await client.fetch_token(
                GOOGLE_TOKEN_URL,
                code=code,
            )
            
            # Get user info - use the token from the response
            # The token_response contains the access_token
            access_token = token_response.get('access_token')
            if not access_token:
                raise ValueError("No access token in response")
            
            # Make request with Authorization header
            headers = {"Authorization": f"Bearer {access_token}"}
            user_info = await client.get(GOOGLE_USERINFO_URL, headers=headers)
            user_data = user_info.json()
        
        email = user_data.get("email")
        
        # Verify authorized email
        if email != GOOGLE_AUTHORIZED_EMAIL:
            logger.warning(f"Unauthorized email attempt: {email}")
            return Response(
                content={"error": "Unauthorized email address"},
                status_code=403,
                media_type="application/json"
            )
        
        # Store authentication in session
        # Ensure email is a string (not bytes)
        email_str = str(email) if email else ""
        await session_store.set(f"{ADMIN_SESSION_KEY}:{session_id}", "true", expires_in=86400)  # 24 hours
        await session_store.set(f"{ADMIN_EMAIL_KEY}:{session_id}", email_str, expires_in=86400)
        
        # Clean up state
        await session_store.delete(f"oauth_state:{session_id}")
        
        logger.info(f"Admin authenticated: {email}")
        
        return Redirect("/admin")
        
    except Exception as e:
        logger.exception("OAuth token exchange failed")
        return Response(
            content={"error": "Authentication failed"},
            status_code=500,
            media_type="application/json"
        )


async def admin_logout(request: Request) -> Redirect:
    """Log out admin user."""
    session_id = request.cookies.get("session_id")
    if session_id:
        await session_store.delete(f"{ADMIN_SESSION_KEY}:{session_id}")
        await session_store.delete(f"{ADMIN_EMAIL_KEY}:{session_id}")
        logger.info(f"Admin logged out: session_id {session_id[:8]}...")
    
    # Get base path for redirect
    base_path = get_base_path(request)
    # Redirect to login page (not /admin/login which starts OAuth)
    response = Redirect(f"{base_path}/admin/login-page")
    # Delete the session cookie
    response.delete_cookie("session_id", path="/")
    return response


async def require_admin_guard(connection: ASGIConnection, handler: BaseRouteHandler) -> None:
    """Guard to require admin authentication."""
    # ASGIConnection has cookies, url, etc. directly accessible
    path = connection.url.path
    logger.debug(f"Checking admin authentication for path: {path}")
    session_id = connection.cookies.get("session_id")
    
    if not session_id:
        logger.warning(f"Admin access attempted without session_id: {path}")
        raise NotAuthorizedException("Not authenticated")
    
    authenticated = await session_store.get(f"{ADMIN_SESSION_KEY}:{session_id}")
    
    if not authenticated:
        logger.warning(f"Admin access attempted without authentication: {path}, session_id: {session_id}")
        raise NotAuthorizedException("Not authenticated")
    
    # Optionally verify email is still authorized
    email_raw = await session_store.get(f"{ADMIN_EMAIL_KEY}:{session_id}")
    if not email_raw:
        logger.warning("No email found in session")
        raise NotAuthorizedException("Unauthorized")
    
    # Handle bytes vs string (MemoryStore may return bytes)
    if isinstance(email_raw, bytes):
        email = email_raw.decode('utf-8')
    else:
        email = str(email_raw)
    
    if email != GOOGLE_AUTHORIZED_EMAIL:
        logger.warning(f"Admin access attempted with unauthorized email: {email} (type: {type(email_raw)})")
        raise NotAuthorizedException("Unauthorized")
    
    logger.debug(f"Admin access granted for: {email}")


