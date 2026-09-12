"""PostgreSQL persistence for lead deduplication, backed by the existing
production table ``public."TLead"`` (not a new table)."""

from .repository import MleadRepository

__all__ = ["MleadRepository"]
