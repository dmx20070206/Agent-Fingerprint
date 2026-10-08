"""Interactive adapter for collecting a human-operated browser fingerprint."""

from __future__ import annotations

import json
import time
import webbrowser
from pathlib import Path
from typing import Callable

from .base_adapter import AgentResult, BaseAgentAdapter


class ManualAdapter(BaseAgentAdapter):
    """Open the task URL in a local browser and wait for the operator.

    The page itself is instrumented by :mod:`sandbox.server`; this adapter only
    owns the human-facing lifecycle.  It deliberately does not close the
    browser because a system browser may contain unrelated tabs.
    """

    adapter_name = "manual"

    def __init__(
        self,
        *,
        browser: str | None = None,
        open_browser: bool = True,
        opener: Callable[[str], bool] | None = None,
        completion_waiter: Callable[[str], object] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(conda_env=None, **kwargs)
        self.browser = browser
        self.open_browser = bool(open_browser)
        self._opener = opener
        self._completion_waiter = completion_waiter or input

    def _open(self, url: str) -> bool:
        if self._opener is not None:
            return bool(self._opener(url))
        if self.browser:
            return bool(webbrowser.get(self.browser).open(url, new=2, autoraise=True))
        return bool(webbrowser.open(url, new=2, autoraise=True))

    def run_task(self, url: str, prompt: str, output_dir: Path | str) -> AgentResult:
        self.validate_task(url, prompt)
        path = self.prepare_output_dir(output_dir)
        started = time.monotonic()
        opened = False
        open_error: str | None = None
        if self.open_browser:
            try:
                opened = self._open(url)
            except (OSError, webbrowser.Error) as exc:
                open_error = f"{type(exc).__name__}: {exc}"

        lines = [
            "Manual fingerprint collection is active.",
            f"URL: {url}",
            f"Task: {prompt}",
        ]
        if not self.open_browser:
            lines.append("Automatic browser opening is disabled; open the URL above manually.")
        elif not opened:
            detail = f" ({open_error})" if open_error else ""
            lines.append(f"The browser could not be opened automatically{detail}; open the URL above manually.")
        message = "\n".join(lines)
        print(message, flush=True)

        try:
            self._completion_waiter(
                "Operate the page in the browser. When finished, keep the browser tab open, "
                "return here, and press Enter; wait for the upload confirmation before closing it... "
            )
        except EOFError as exc:
            raise RuntimeError(
                "manual mode requires interactive stdin; press Enter to finish, "
                "or provide stdin when running non-interactively"
            ) from exc

        duration = time.monotonic() - started
        document = {
            "output": "Manual browser interaction completed.",
            "task_success": True,
            "task_status": "manual_complete",
            "browser_open_requested": self.open_browser,
            "browser_opened": opened,
            "browser": self.browser,
            "open_error": open_error,
        }
        (path / "result.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = AgentResult(
            adapter=self.adapter_name,
            command=("manual", url),
            output_dir=path,
            returncode=0,
            stdout=message + "\nManual browser interaction completed.\n",
            stderr="",
            duration_seconds=duration,
            task_success=True,
            task_status="manual_complete",
        )
        (path / "stdout.log").write_text(result.stdout, encoding="utf-8")
        (path / "stderr.log").write_text("", encoding="utf-8")
        (path / "adapter_result.json").write_text(
            json.dumps(result.to_dict(include_output=False), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
