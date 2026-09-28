"""Registered storefront insights. Import a module here to expose it as an MCP tool."""

from . import after_hours, daily_recap, explain_change, staffing  # noqa: F401
from .base import REGISTRY, InsightContext, InsightResult  # noqa: F401
