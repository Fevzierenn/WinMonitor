"""AI usage providers: each runs one external tool and returns its raw report.

A raw :class:`~winmonitor.providers.base.AIUsageReport` names the tool that
produced it, and :func:`normalize_report` hands it to that tool's normalizer to
get the typed :class:`~winmonitor.models.usage.UsageReport` the UI renders.
Supporting another tool means adding its normalizer to :data:`NORMALIZERS`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..models.usage import UsageReport
from . import ccusage_normalize
from .base import AIUsageError, AIUsageReport

__all__ = ["NORMALIZERS", "Normalizer", "normalize_report"]

Normalizer = Callable[[AIUsageReport], UsageReport]

#: Provider name (``AIUsageReport.provider``) to the function that normalizes it.
NORMALIZERS: Mapping[str, Normalizer] = {"ccusage": ccusage_normalize.normalize}


def normalize_report(report: AIUsageReport) -> UsageReport:
    """Normalize ``report`` with the normalizer registered for its provider."""
    normalizer = NORMALIZERS.get(report.provider)
    if normalizer is None:
        raise AIUsageError(
            "unsupported", f"No normalizer is registered for AI usage provider {report.provider!r}."
        )
    return normalizer(report)
