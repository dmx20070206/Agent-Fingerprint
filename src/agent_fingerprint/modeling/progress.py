"""Immediately visible training progress, separate from CLI JSON results."""

import sys


def report_progress(message):
    print(message, file=sys.stderr, flush=True)
