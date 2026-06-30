"""Request-scoped caller identity, derived from the validated token.

user_id NEVER comes from a tool argument. The auth middleware validates the
bearer token, then sets the identity here for the duration of the request.
Tool functions read current_user_id() to scope every storage call.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


class Unauthenticated(Exception):
    pass


@dataclass(frozen=True)
class _Identity:
    user_id: str
    email: str | None
    display_name: str | None


_current: contextvars.ContextVar[_Identity | None] = contextvars.ContextVar(
    "wingman_identity", default=None
)


def set_current_user(user_id: str, email: str | None, display_name: str | None) -> contextvars.Token:
    return _current.set(_Identity(user_id, email, display_name))


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def _get() -> _Identity:
    ident = _current.get()
    if ident is None:
        raise Unauthenticated("no authenticated user in context")
    return ident


def current_user_id() -> str:
    return _get().user_id


def current_email() -> str | None:
    return _get().email


def current_display_name() -> str | None:
    return _get().display_name
