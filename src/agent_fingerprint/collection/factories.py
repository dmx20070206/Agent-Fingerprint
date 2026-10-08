"""End-to-end data-collection cycle for the Agent-Fingerprint project.

``PipelineRunner`` owns the lifecycle described in the project design:

    sandbox HTTP server -> LiteLLM gateway -> network probe -> Agent adapter
    -> artifact collection -> reverse-order cleanup

The default tests use fake adapters/tools, so this orchestration layer can be
verified without a paid LLM API or browser installation.  Real adapters can be
passed through ``adapter_factory`` in exactly the same way.
"""

from __future__ import annotations


import inspect
import os
from pathlib import Path
from typing import Any, Callable

from agent_fingerprint.adapters import (
    AgentEAdapter,
    AutoGenAdapter,
    BrowserUseAdapter,
    ManualAdapter,
    MockAgentAdapter,
    SkyvernAdapter,
    WebVoyagerAdapter,
)
from agent_fingerprint.collection.gateway import GatewayConfig, LiteLLMGateway
from agent_fingerprint.collection.probes.traffic_sniffer import TrafficSniffer

from agent_fingerprint.storage.schema import *

def _call_factory(factory: Callable[..., Any], **context: Any) -> Any:
    """Call a user factory with only the arguments it declares.

    This supports old ``lambda: ...`` test factories and newer factories such
    as ``lambda run_dir: ...``/``lambda model, gateway: ...``.  Importantly,
    exceptions raised by the factory itself are not mistaken for a signature
    mismatch.
    """

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(**context)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return factory(**context)
    positional: list[Any] = []
    keyword: dict[str, Any] = {}
    for parameter in parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        has_value = parameter.name in context and context[parameter.name] is not None
        value = context.get(parameter.name)
        if not has_value and parameter.default is inspect.Parameter.empty:
            # Preserve the historical one-positional-argument path API even
            # when the callable also declares keyword-only context (for
            # example ``factory(path, *, model=None)``).
            value = context.get("path", context.get("output_dir", context.get("run_dir")))
            has_value = value is not None
        if not has_value:
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        elif parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and parameter.default is inspect.Parameter.empty:
            positional.append(value)
        else:
            keyword[parameter.name] = value
    return factory(*positional, **keyword)


def _infer_provider_key_env(upstream_model: str | None) -> str | None:
    """Infer the conventional provider credential variable from a model id."""

    value = (upstream_model or "").strip().lower()
    provider = value.split("/", 1)[0] if "/" in value else value.split("-", 1)[0]
    return {
        "deepseek": "DEEPSEEK_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "RELAY_OPENAI_API_KEY",
    }.get(provider)


def _gateway_key(gateway: Any) -> str | None:
    if gateway is None:
        return None
    getter = getattr(gateway, "auth_key", None)
    if callable(getter):
        try:
            getter = getter()
        except Exception:
            getter = None
    if getter:
        return str(getter)
    env_name = getattr(gateway, "master_key_env", None)
    runtime = getattr(gateway, "_runtime_env", {})
    return (runtime.get(env_name) if env_name else None) or (os.environ.get(env_name) if env_name else None)


class ResourceFactories:
    def _make_gateway(self, run_dir: Path) -> LiteLLMGateway | None:
        if self.gateway_factory:
            return _call_factory(self.gateway_factory, run_dir=run_dir)
        if self.gateway:
            return self.gateway
        if self.gateway_config is None:
            return None
        config = (
            self.gateway_config
            if isinstance(self.gateway_config, GatewayConfig)
            else GatewayConfig.load(self.gateway_config)
        )
        # Never mutate the source config in configs/. Each cycle gets a private
        # copy so model switching and concurrent runs are isolated.
        isolated = GatewayConfig(run_dir / "logs" / "litellm.config.yaml", config.document())
        if self.gateway_configurator:
            self.gateway_configurator(isolated)
        options = dict(self.gateway_options)
        options.setdefault("output_dir", run_dir / "logs")
        gateway = LiteLLMGateway(isolated, **options)
        return gateway


    @staticmethod
    def _default_route_alias(agent_name: str, model: str | None) -> str:
        if model:
            return model
        # Both supported adapters speak the OpenAI-compatible API when a
        # gateway is present.  Keeping one stable alias makes matrix results
        # comparable across frameworks.
        return "chat-gpt"


    def _configure_gateway_route(
        self,
        gateway: Any,
        *,
        agent_name: str,
        model: str | None,
        upstream_model: str | None,
        provider_key_env: str | None,
        route_alias: str | None,
    ) -> dict[str, Any] | None:
        """Apply a per-run route switch before the proxy is started.

        The source YAML is copied by :meth:`_make_gateway`; this method only
        mutates that private copy.  A custom gateway may expose the same
        ``config``/``switch_route`` methods, or simply ignore route metadata.
        """

        if not upstream_model:
            return None
        alias = route_alias or self._default_route_alias(agent_name, model)
        # A provider switch must also switch credentials.  If callers omit
        # --provider-key-env, infer the conventional key from the provider
        # prefix instead of silently retaining the source route's key (for
        # example RELAY_OPENAI_API_KEY when routing chat-gpt to DeepSeek).
        effective_key_env = provider_key_env or _infer_provider_key_env(upstream_model)
        config = getattr(gateway, "config", None)
        if config is not None and hasattr(config, "set_route") and not _is_running(gateway):
            existing = getattr(config, "routes", {}).get(alias)
            params = dict(getattr(existing, "params", {}) or {}) if existing else {}
            api_base = getattr(existing, "api_base", None) if existing else None
            key_env = effective_key_env or (getattr(existing, "api_key_env", None) if existing else None)
            effective_key_env = key_env
            config.set_route(alias, upstream_model, api_key_env=key_env, api_base=api_base, **params)
        elif hasattr(gateway, "switch_route"):
            # An externally running gateway can still be switched safely via
            # its public restart operation.
            existing = getattr(config, "routes", {}).get(alias) if config is not None else None
            key_env = effective_key_env or (getattr(existing, "api_key_env", None) if existing else None)
            effective_key_env = key_env
            route_kwargs: dict[str, Any] = {"api_key_env": key_env, "restart": True}
            # Preserve provider-specific settings (api_base, timeout, headers,
            # deployment parameters, ...) when a shared/external gateway is
            # restarted.  Dropping them silently routes a request to a
            # different endpoint than the manifest claims.
            if existing is not None:
                existing_base = getattr(existing, "api_base", None)
                if existing_base:
                    route_kwargs["api_base"] = existing_base
                route_kwargs.update(dict(getattr(existing, "params", {}) or {}))
            gateway.switch_route(alias, upstream_model, **route_kwargs)
        else:
            # Reporting a route in the manifest without actually applying it
            # is worse than failing early: it silently invalidates comparisons
            # between Agent/framework runs.  Lightweight mock gateways must
            # therefore be used without --upstream-model (or implement the
            # same route-switching surface).
            raise RuntimeError(
                "gateway does not support dynamic route switching; provide a "
                "GatewayConfig or switch_route() implementation"
            )
        return {
            "alias": alias,
            "upstream_model": upstream_model,
            "provider_key_env": effective_key_env,
            "applied": True,
        }


    def _make_adapter(
        self,
        name: str,
        *,
        model: str | None = None,
        upstream_model: str | None = None,
        gateway: LiteLLMGateway | None = None,
        run_dir: Path | None = None,
        run_id: str | None = None,
    ) -> Any:
        if name in self.adapter_factory:
            factory = self.adapter_factory[name]
            # Custom factories may opt into context-aware construction.  Keep
            # zero-argument factories backwards compatible without swallowing a
            # TypeError raised *inside* a factory.
            # Keep the factory construction context compatible with existing
            # extensions. Built-in execution itself writes under logs/agent.
            agent_output = run_dir / "agent" if run_dir is not None else None
            return _call_factory(
                factory,
                model=model,
                gateway=gateway,
                name=name,
                run_dir=run_dir,
                run_id=run_id,
                output_dir=agent_output,
                path=agent_output,
            )
        options = dict(self.adapter_options.get(name, {}))
        if name == "webvoyager":
            options.setdefault("api_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return WebVoyagerAdapter(
                **options,
            )
        if name == "browseruse":
            options.setdefault("llm_provider", "openai" if gateway else "browser_use")
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            # Keep the provider model visible to Browser-use.  The public
            # alias can be provider-neutral (for example ``chat-gpt``) while
            # the gateway routes it to DeepSeek, whose response-format
            # compatibility differs from OpenAI.
            options.setdefault("upstream_model", upstream_model)
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return BrowserUseAdapter(
                **options,
            )
        if name == "mock":
            options.setdefault("gateway_url", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("gateway_key", _gateway_key(gateway))
            options.setdefault("model", model or "mock-model")
            options.setdefault("run_id", run_id)
            return MockAgentAdapter(**options)
        if name == "manual":
            return ManualAdapter(**options)
        if name == "skyvern":
            options.setdefault("api_key", os.environ.get("SKYVERN_API_KEY"))
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("llm_api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("llm_api_key", _gateway_key(gateway))
            return SkyvernAdapter(**options)
        if name == "agente":
            options.setdefault("llm_model", model or ("chat-gpt" if gateway else None))
            options.setdefault("llm_api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("llm_api_key", _gateway_key(gateway))
            return AgentEAdapter(**options)
        if name == "autogen":
            options.setdefault("llm_model", model or "gpt-4o")
            capability_model = upstream_model
            if capability_model is None and gateway is not None:
                config = getattr(gateway, "config", None)
                route = getattr(config, "routes", {}).get(model) if config is not None and model else None
                capability_model = getattr(route, "model", None)
            options.setdefault("upstream_model", capability_model)
            options.setdefault("api_base", getattr(gateway, "base_url", None) if gateway else None)
            options.setdefault("api_key", _gateway_key(gateway))
            return AutoGenAdapter(**options)
        raise ValueError(f"unsupported agent: {name}")


    def _make_sniffer(self, run_dir: Path) -> TrafficSniffer | None:
        if not self.use_network_probe or not self.collect_l1:
            return None
        if self.sniffer_factory:
            network_dir = run_dir / "logs" / "network"
            return _call_factory(
                self.sniffer_factory,
                output_dir=network_dir,
                network_dir=network_dir,
                path=network_dir,
                run_dir=run_dir,
            )
        return TrafficSniffer(run_dir / "logs" / "network", backend="tcpdump", interface="any")




def _is_running(resource: Any) -> bool:
    value = getattr(resource, "running", None)
    if isinstance(value, bool):
        return value
    checker = getattr(resource, "is_running", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            pass
    process = getattr(resource, "process", None)
    if process is not None and hasattr(process, "poll"):
        try:
            return process.poll() is None
        except Exception:
            pass
    return bool(getattr(resource, "started", False))
