"""Per-source totals across a whole AI usage report (the BY SOURCE summary)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..providers.base import SOURCE_ID, AIUsageReport, source_name

__all__ = ["SourceUsage", "usage_by_source"]

_TOKEN_KEYS = ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens")


@dataclass(frozen=True)
class SourceUsage:
    """Token and estimated-cost totals for one agent across a whole report."""

    source: str
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost: float | None
    models: tuple[str, ...]

    @property
    def name(self) -> str:
        return source_name(self.source)


def _number(value: Any) -> float | None:
    # bool is an int subclass; a stray true/false must not count as a token.
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usage_by_source(report: AIUsageReport) -> tuple[SourceUsage, ...]:
    """Sum each agent's usage over every row of ``report``, largest first.

    Rows are attributed from ccusage's own JSON only: the ``agents`` list that
    ``--by-agent`` adds to unified rows, a row's ``agent`` field (sessions and
    single-source reports), or the report's source filter. A unified row with
    none of those cannot be split, so an empty tuple means "no breakdown", not
    "no usage".
    """
    totals: dict[str, dict[str, Any]] = {}

    def add(agent: str, row: dict[str, Any]) -> None:
        entry = totals.setdefault(
            agent, {"tokens": dict.fromkeys(_TOKEN_KEYS, 0), "total": 0, "cost": None, "models": {}}
        )
        parts = {key: int(_number(row.get(key)) or 0) for key in _TOKEN_KEYS}
        for key, value in parts.items():
            entry["tokens"][key] += value
        total = _number(row.get("totalTokens"))
        entry["total"] += int(total) if total is not None else sum(parts.values())
        # Agents and ccusage versions disagree on the cost field's name.
        for key in ("totalCost", "costUSD", "cost"):
            cost = _number(row.get(key))
            if cost is not None:
                entry["cost"] = (entry["cost"] or 0.0) + cost
                break
        models = row.get("modelsUsed", row.get("models"))
        if isinstance(models, (list, dict)):
            # dict.fromkeys keeps first-seen order while de-duplicating.
            entry["models"].update(dict.fromkeys(str(model) for model in models))

    for row in report.rows:
        agents = row.get("agents")
        if isinstance(agents, list) and agents:
            for item in agents:
                if isinstance(item, dict) and isinstance(item.get("agent"), str):
                    add(item["agent"], item)
        elif isinstance(row.get("agent"), str) and row["agent"] != "all":
            add(row["agent"], row)
        elif report.source is not None:
            add(report.source, row)
    usage = (
        SourceUsage(
            source=agent,
            total_tokens=entry["total"],
            input_tokens=entry["tokens"]["inputTokens"],
            output_tokens=entry["tokens"]["outputTokens"],
            cache_creation_tokens=entry["tokens"]["cacheCreationTokens"],
            cache_read_tokens=entry["tokens"]["cacheReadTokens"],
            cost=entry["cost"],
            models=tuple(entry["models"]),
        )
        for agent, entry in totals.items()
        if agent != "all" and SOURCE_ID.fullmatch(agent)
    )
    return tuple(sorted(usage, key=lambda item: (-item.total_tokens, item.source)))
