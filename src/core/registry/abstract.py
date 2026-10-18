from abc import ABC, abstractmethod


class BaseRegistry[T](ABC):
    @abstractmethod
    def __getitem__(self, name: str) -> T: ...
