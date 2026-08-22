"""PostgreSQL persistence for lead deduplication, backed by the existing
production table ``public.mlead`` (not a new table)."""

from .repository import MleadRepository

__all__ = ["MleadRepository"]
