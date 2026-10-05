"""Structured application errors (Section 28).

Routers/services raise these; a single set of FastAPI exception handlers
(registered in main.py) converts them into a consistent JSON shape and never
leaks SQL, stack traces, or filesystem paths to the client.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all structured application errors."""

    code: str = "INTERNAL_SERVER_ERROR"
    status_code: int = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)


class ValidationAppError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class AuthenticationError(AppError):
    code = "AUTHENTICATION_ERROR"
    status_code = 401


class AuthorizationError(AppError):
    code = "AUTHORIZATION_ERROR"
    status_code = 403


class ResourceNotFoundError(AppError):
    code = "RESOURCE_NOT_FOUND"
    status_code = 404


class DuplicateResourceError(AppError):
    code = "DUPLICATE_RESOURCE"
    status_code = 409


class BusinessRuleViolationError(AppError):
    code = "BUSINESS_RULE_VIOLATION"
    status_code = 422


class ImportError_(AppError):
    """Named with a trailing underscore to avoid shadowing the builtin ImportError."""

    code = "IMPORT_ERROR"
    status_code = 422


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    status_code = 429
