"""Local LiteLLM gateway used to route all Agent model requests."""

__all__ = ["GatewayConfig", "LiteLLMGateway", "ModelRoute", "MockOpenAIGateway"]


def __getattr__(name):
    # Lazy imports keep ``python -m gateway.litellm_gateway`` free of the
    # runpy duplicate-module warning while preserving a convenient package API.
    if name in {"GatewayConfig", "LiteLLMGateway", "ModelRoute"}:
        from .litellm_gateway import GatewayConfig, LiteLLMGateway, ModelRoute

        return {"GatewayConfig": GatewayConfig, "LiteLLMGateway": LiteLLMGateway, "ModelRoute": ModelRoute}[name]
    if name == "MockOpenAIGateway":
        from .mock_openai import MockOpenAIGateway

        return MockOpenAIGateway
    raise AttributeError(name)
