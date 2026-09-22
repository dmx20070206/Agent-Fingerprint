"""Process-isolated adapters for the supported Web Agent frameworks."""

from .base_adapter import AgentAdapterError, AgentExecutionError, AgentInterruptedError, AgentResult, BaseAgentAdapter
from .agente_adapter import AgentEAdapter
from .mock_adapter import MockAgentAdapter
from .manual_adapter import ManualAdapter
from .browseruse_adapter import BrowserUseAdapter
from .webvoyager_adapter import WebVoyagerAdapter
from .skyvern_adapter import SkyvernAdapter
from .autogen_adapter import AutoGenAdapter

__all__ = [
    "AgentAdapterError",
    "AgentExecutionError",
    "AgentInterruptedError",
    "AgentResult",
    "BaseAgentAdapter",
    "AgentEAdapter",
    "MockAgentAdapter",
    "ManualAdapter",
    "BrowserUseAdapter",
    "WebVoyagerAdapter",
    "SkyvernAdapter",
    "AutoGenAdapter",
]
