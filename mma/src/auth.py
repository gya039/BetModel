"""
auth.py — API authentication and rate limiting for admin mutation endpoints.

Authentication
──────────────
Token-based via the OCTAGON_ADMIN_TOKEN environment variable.
Token is sent as:
  • X-Admin-Token: <token>   request header
  • ?token=<token>            query parameter (fallback)

If OCTAGON_ADMIN_TOKEN is not set, admin endpoints are accessible but a
warning is logged on every request.  Set the variable in production.

Rate Limiting
─────────────
Sliding-window in-memory rate limiter (resets on process restart).
Default limits for admin endpoints: 20 calls / 60 seconds per IP.
Default limits for the settlement endpoint: 10 calls / 60 seconds per IP.

Usage (Flask)
─────────────
    from auth import require_admin_token, rate_limit

    @app.route("/api/integrity/rebuild-bankroll", methods=["POST"])
    @require_admin_token
    @rate_limit(max_calls=5, window_seconds=60)
    def rebuild():
        ...
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from functools import wraps
from typing import Callable

# ── configuration ─────────────────────────────────────────────────────────────

_ADMIN_TOKEN_ENV  = "OCTAGON_ADMIN_TOKEN"
_UNSET_WARNING_LOGGED = False


def _admin_token() -> str | None:
    return os.environ.get(_ADMIN_TOKEN_ENV)


# ── auth decorator ────────────────────────────────────────────────────────────

def require_admin_token(f: Callable) -> Callable:
    """
    Flask route decorator that enforces admin token authentication.

    Allows the request through if:
    • OCTAGON_ADMIN_TOKEN is not set (dev/local mode — logs a warning once)
    • The provided token matches OCTAGON_ADMIN_TOKEN

    Returns 401 if the token is wrong, 403 if token is required but absent.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, jsonify
        import log as _log

        global _UNSET_WARNING_LOGGED
        token = _admin_token()

        if not token:
            if not _UNSET_WARNING_LOGGED:
                _log.warning(
                    "admin_auth_disabled",
                    hint=f"Set {_ADMIN_TOKEN_ENV} env var to require auth on admin endpoints",
                    endpoint=request.endpoint,
                )
                _UNSET_WARNING_LOGGED = True
            return f(*args, **kwargs)

        provided = (
            request.headers.get("X-Admin-Token") or
            request.args.get("token")
        )
        if not provided:
            return jsonify({
                "error": "Unauthorized",
                "hint": "Provide X-Admin-Token header",
            }), 403
        if provided != token:
            _log.warning("admin_auth_rejected", endpoint=request.endpoint,
                         remote=request.remote_addr)
            return jsonify({"error": "Invalid admin token"}), 401

        return f(*args, **kwargs)

    return decorated


# ── rate limiter ──────────────────────────────────────────────────────────────

_rl_state: dict[str, list[float]] = defaultdict(list)
_rl_lock  = threading.Lock()


def _check_rate(key: str, max_calls: int, window_seconds: int) -> bool:
    """
    Sliding-window rate check.
    Returns True if the call is allowed, False if rate-limited.
    """
    now = time.monotonic()
    with _rl_lock:
        calls = _rl_state[key]
        # Prune entries outside the window
        _rl_state[key] = [t for t in calls if now - t < window_seconds]
        if len(_rl_state[key]) >= max_calls:
            return False
        _rl_state[key].append(now)
        return True


def rate_limit(max_calls: int = 20, window_seconds: int = 60) -> Callable:
    """
    Flask route decorator for sliding-window rate limiting.

    The rate limit key is: "<endpoint>:<remote_addr>"
    Returns 429 Too Many Requests when the limit is exceeded.

    Example:
        @rate_limit(max_calls=5, window_seconds=60)
        def my_view():
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import request, jsonify
            key = f"{request.endpoint}:{request.remote_addr}"
            if not _check_rate(key, max_calls, window_seconds):
                return jsonify({
                    "error": "Too many requests",
                    "limit": max_calls,
                    "window_seconds": window_seconds,
                }), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── admin-only combined decorator ─────────────────────────────────────────────

def admin_action(max_calls: int = 10, window_seconds: int = 60) -> Callable:
    """
    Combines require_admin_token + rate_limit into a single decorator.
    Default: 10 calls per minute per IP.

    @admin_action(max_calls=5)
    def sensitive_endpoint(): ...
    """
    def decorator(f: Callable) -> Callable:
        # Apply inner-to-outer: rate_limit wraps f first, then auth wraps that
        return require_admin_token(rate_limit(max_calls, window_seconds)(f))
    return decorator
