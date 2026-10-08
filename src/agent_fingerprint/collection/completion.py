"""Framework-independent completion evidence for local browser tasks.

Pages own the workflow completion contract; the collector only observes it.
Task parameters (names, seats, selected products, etc.) are deliberately not
compared with the prompt. Missing evidence is never an implicit pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from uuid import uuid4

VERIFIED_AGENTS = frozenset({"agente", "autogen", "browseruse", "skyvern", "webvoyager"})


@dataclass(frozen=True, slots=True)
class CompletionResult:
    status: str = "unknown"
    reason: str = "No page completion evidence was received"
    url: str | None = None
    page_status: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict:
        return {"source": "page_completion_contract", **asdict(self)}


class CompletionVerifier:
    """Keep the latest document's ordered observations, isolated by run.

    Document tokens prevent late uploads from a previous navigation from
    replacing the current page's state. Upload retries are idempotent.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._documents: dict[str, tuple[str, int, CompletionResult]] = {}

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._documents.pop(run_id, None)

    def begin_document(self, run_id: str, url: str) -> str:
        token = uuid4().hex
        with self._lock:
            self._documents[run_id] = (token, 0, CompletionResult(url=url))
        return token

    def observe(self, run_id: str, token: str, sequence: int, page_status: str) -> bool:
        if type(sequence) is not int or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if page_status not in {"pending", "passed", "failed", "unknown"}:
            raise ValueError("unsupported page completion status")
        with self._lock:
            current = self._documents.get(run_id)
            if current is None or current[0] != token:
                return False
            if sequence <= current[1]:
                return True
            status, reason = {
                "passed": ("passed", "Page reached its completed workflow state"),
                "failed": ("failed", "Page reported task failure"),
                "pending": ("failed", "Page workflow is still pending"),
                "unknown": ("unknown", "Page does not expose a completion contract"),
            }[page_status]
            result = CompletionResult(status, reason, current[2].url, page_status)
            self._documents[run_id] = (token, sequence, result)
            return True

    def result(self, run_id: str) -> CompletionResult:
        with self._lock:
            current = self._documents.get(run_id)
            return current[2] if current else CompletionResult()
