"""SportAdapter protocol + register() / get(). Each sport implements only this."""
from typing import Protocol, runtime_checkable

_ADAPTERS = {}


@runtime_checkable
class SportAdapter(Protocol):
    sport: str

    def offers(self, as_of): ...
    def predict(self, offer): ...
    def market(self, offer): ...
    def flags(self, offer): ...
    def settle(self, offer): ...
    def match_key(self, offer): ...
    def team_key(self, offer): ...
    def correlation(self, a, b): ...


def register(adapter):
    if not isinstance(adapter, SportAdapter):
        raise TypeError("adapter does not implement SportAdapter")
    if adapter.sport in _ADAPTERS:
        raise ValueError(f"{adapter.sport} is already registered")
    _ADAPTERS[adapter.sport] = adapter
    return adapter


def get(sport):
    return _ADAPTERS[sport]


def sports():
    return sorted(_ADAPTERS)


def clear():
    _ADAPTERS.clear()
