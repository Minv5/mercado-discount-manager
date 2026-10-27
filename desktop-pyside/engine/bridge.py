"""Backward-compatibility bridge for EngineController."""
from __future__ import annotations

from typing import Any
from .controller import EngineController, validate_seller_campaign_input


class _EngineBridgeMeta(type):
    @property
    def _instance(cls) -> Any:
        return EngineController._instance

    @_instance.setter
    def _instance(cls, value: Any) -> None:
        EngineController._instance = value


class EngineBridge(EngineController, metaclass=_EngineBridgeMeta):
    """Backward-compatible subclass of EngineController.

    All new code should import and use EngineController directly.
    """

    @classmethod
    def get_instance(cls) -> "EngineBridge":
        if EngineController._instance is None:
            EngineController._instance = EngineBridge()
        return EngineController._instance  # type: ignore[return-value]


__all__ = ["EngineBridge", "EngineController", "validate_seller_campaign_input"]
