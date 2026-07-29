from abc import ABC , abstractmethod

class BaseClass(ABC):

    @abstractmethod
    def execute(self, context):
        pass
