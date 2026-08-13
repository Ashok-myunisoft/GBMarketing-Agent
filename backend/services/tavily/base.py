"""
Provider-agnostic search interface. TavilySearchService is the only concrete
implementation today; a Brave/Bing/SerpAPI provider can be added later by
implementing this same interface, without touching any caller.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchResult:
    """One search hit, provider-agnostic."""

    url: str
    title: str = ""
    content: str = ""
    score: float = 0.0
    raw_content: Optional[str] = None


class SearchProvider(ABC):
    """Base class every search provider implements."""

    @property
    def is_configured(self) -> bool:
        """Whether this provider is ready to be called (e.g. has an API key).
        Defaults to True; a provider that needs credentials (TavilySearchService)
        overrides this so callers can skip it cleanly instead of erroring."""
        return True

    @abstractmethod
    def search(self, query: str, max_results: Optional[int] = None, **kwargs) -> "list[SearchResult]":
        ...
