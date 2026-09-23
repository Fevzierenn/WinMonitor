"""Turn a raw ccusage report into the normalized usage model.

This is the only place that knows ccusage's JSON field names.  They vary by
ccusage version and by agent: the period is ``period`` in 20.x but ``date``,
``week``, ``month`` or ``sessionId`` before; cost is ``totalCost``,
``costUSD`` or ``cost``; models are ``modelsUsed`` or ``models``, as a list or
a map; the project and session times may sit inside ``metadata``.  Every
variant is read here so the renderer and the BY SOURCE summary only see
:class:`~winmonitor.models.usage.UsageReport`.

Fields this module does not know are kept in ``UsageRow.extra`` when every row
has a plain scalar for them, so a column a newer ccusage adds is still shown.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from ..models.usage import ExtraValue, TokenCounts, UsageReport, UsageRow
from .base import AIUsageReport

__all__ = ["normalize"]

#: Where each report type keeps its period, in order of preference.
_PERIOD_KEYS = {
    "daily": ("period", "date"),
    "weekly": ("period", "week"),
    "monthly": ("period", "month"),
    "session": ("period", "sessionId", "session"),
}
_TOKEN_KEYS = {
    "input": "inputTokens",
    "output": "outputTokens",
    "cache_creation": "cacheCreationTokens",
    "cache_read": "cacheReadTokens",
    "total": "totalTokens",
}
_COST_KEYS = ("totalCost", "costUSD", "cost")
_TOTAL_COST_KEYS = ("totalCost", "totalCostUSD", "costUSD")
_MODEL_KEYS = ("modelsUsed", "models")
_PROJECT_KEYS = ("projectPath", "project")
#: Nested structures ccusage adds for detail views; never a column.
_STRUCTURE_KEYS = frozenset({"metadata", "agents", "modelBreakdowns", "breakdown"})


def _is_number(value: Any) -> bool:
    # bool is an int subclass; a stray true/false must not count as a number.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count(value: Any) -> int | None:
    if not _is_number(value) or not math.isfinite(value):
        return None
    return int(value)


def _amount(value: Any) -> float | None:
    return float(value) if _is_number(value) else None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return str(value) if _is_number(value) else None


def _models(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, str):
        return (value,)
    # A map is keyed by model name; its values are per-model details.
    if isinstance(value, (list, dict)):
        return tuple(str(model) for model in value)
    return None


def _pick[T](
    row: Mapping[str, Any], keys: tuple[str, ...], convert: Callable[[Any], T | None]
) -> tuple[T | None, set[str]]:
    """The first of ``keys`` holding a usable value, and the keys it accounts for.

    An alternative key that holds a different, non-null value is not accounted
    for, so it stays visible as an extra column instead of disappearing.
    """
    for key in keys:
        value = convert(row.get(key))
        if value is not None:
            return value, {key} | {other for other in keys if row.get(other) is None}
    return None, {key for key in keys if row.get(key) is None}


def _unrenderable(rows: tuple[dict[str, Any], ...]) -> set[str]:
    """Keys that hold a list or object in some row and so cannot be a column."""
    return {
        key
        for row in rows
        for key, value in row.items()
        if value is not None and not isinstance(value, (str, int, float, bool))
    }


def _row(
    raw: Mapping[str, Any],
    report_type: str,
    unrenderable: set[str] | None,
) -> UsageRow:
    """Normalize one row; ``unrenderable`` is ``None`` for a nested per-agent row."""
    known: set[str] = set(_STRUCTURE_KEYS)
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    period, used = _pick(raw, _PERIOD_KEYS.get(report_type, ("period",)), _text)
    known |= used
    agent = raw.get("agent")
    if agent is None or isinstance(agent, str):
        known.add("agent")
    project, used = _pick(raw, _PROJECT_KEYS, _text)
    known |= used
    if project is None:
        project = _text(metadata.get("projectPath"))
    models, used = _pick(raw, _MODEL_KEYS, _models)
    known |= used
    counts: dict[str, int | None] = {}
    for field, key in _TOKEN_KEYS.items():
        counts[field], used = _pick(raw, (key,), _count)
        known |= used
    cost, used = _pick(raw, _COST_KEYS, _amount)
    known |= used
    activity: dict[str, str | None] = {}
    for key in ("firstActivity", "lastActivity"):
        activity[key], used = _pick(raw, (key,), _text)
        known |= used
        if activity[key] is None:
            activity[key] = _text(metadata.get(key))

    by_source: tuple[UsageRow, ...] = ()
    extra: dict[str, ExtraValue] = {}
    if unrenderable is not None:
        agents = raw.get("agents")
        if isinstance(agents, list):
            by_source = tuple(
                _row(item, report_type, None)
                for item in agents
                if isinstance(item, dict) and isinstance(item.get("agent"), str)
            )
        extra = {
            key: value for key, value in raw.items() if key not in known and key not in unrenderable
        }
    return UsageRow(
        period=period,
        source=agent if isinstance(agent, str) and agent != "all" else None,
        all_sources=agent == "all",
        project=project,
        models=models,
        tokens=TokenCounts(**counts),
        cost=cost,
        first_activity=activity["firstActivity"],
        last_activity=activity["lastActivity"],
        by_source=by_source,
        extra=extra,
    )


def normalize(report: AIUsageReport) -> UsageReport:
    """Map every ccusage row and the ccusage totals onto the usage model."""
    unrenderable = _unrenderable(report.rows)
    totals = report.totals
    return UsageReport(
        report_type=report.report_type,
        source=report.source,
        provider=report.provider,
        rows=tuple(_row(row, report.report_type, unrenderable) for row in report.rows),
        totals=TokenCounts(
            **{field: _count(totals.get(key)) for field, key in _TOKEN_KEYS.items()}
        ),
        total_cost=_pick(totals, _TOTAL_COST_KEYS, _amount)[0],
    )
