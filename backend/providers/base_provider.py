from abc import ABC, abstractmethod
from typing import List

from schemas.company import Company
from schemas.search_request import SearchRequest


class BaseProvider(ABC):
    """
    Base class for all search providers.
    """

    @abstractmethod
    def search(self, request: SearchRequest) -> List[Company]:
        pass