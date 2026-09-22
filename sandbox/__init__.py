"""Local pages and HTTP server used as deterministic Web Agent fixtures."""

__all__ = ["STATIC_DIR", "TraceStore", "create_server", "serve"]


def __getattr__(name):
    # Lazy imports avoid the runpy duplicate-module warning for
    # ``python -m sandbox.server`` while retaining convenient package imports.
    if name in __all__:
        from .server import STATIC_DIR, TraceStore, create_server, serve

        return {
            "STATIC_DIR": STATIC_DIR,
            "TraceStore": TraceStore,
            "create_server": create_server,
            "serve": serve,
        }[name]
    raise AttributeError(name)
