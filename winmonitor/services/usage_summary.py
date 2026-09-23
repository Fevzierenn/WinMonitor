"""Per-source totals across a whole AI usage report (the BY SOURCE summary)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.usage import UsageReport, UsageRow
from ..providers.base import SOURCE_ID, source_name

__all__ = ["SourceUsage", "usage_by_source"]


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


@dataclass
class _Tally:
    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    total: int = 0
    cost: float | None = None
    # A dict keeps first-seen order while de-duplicating.
    models: dict[str, None] = field(default_factory=dict)

    def add(self, row: UsageRow) -> None:
        tokens = row.tokens
        parts = (
            tokens.input or 0,
            tokens.output or 0,
            tokens.cache_creation or 0,
            tokens.cache_read or 0,
        )
        self.input += parts[0]
        self.output += parts[1]
        self.cache_creation += parts[2]
        self.cache_read += parts[3]
        self.total += tokens.total if tokens.total is not None else sum(parts)
        if row.cost is not None:
            self.cost = (self.cost or 0.0) + row.cost
        self.models.update(dict.fromkeys(row.models or ()))


def usage_by_source(report: UsageReport) -> tuple[SourceUsage, ...]:
    """Sum each agent's usage over every row of ``report``, largest first.

    Rows are attributed from what the tool itself reported: a combined row's
    per-source split (ccusage ``--by-agent``), a row's own source (sessions and
    single-source reports), or the report's source filter. A combined row with
    none of those cannot be split, so an empty tuple means "no breakdown", not
    "no usage".
    """
    tallies: dict[str, _Tally] = {}
    for row in report.rows:
        if row.by_source:
            for item in row.by_source:
                if item.source is not None:
                    tallies.setdefault(item.source, _Tally()).add(item)
        elif row.source is not None:
            tallies.setdefault(row.source, _Tally()).add(row)
        elif report.source is not None:
            tallies.setdefault(report.source, _Tally()).add(row)
    usage = (
        SourceUsage(
            source=source,
            total_tokens=tally.total,
            input_tokens=tally.input,
            output_tokens=tally.output,
            cache_creation_tokens=tally.cache_creation,
            cache_read_tokens=tally.cache_read,
            cost=tally.cost,
            models=tuple(tally.models),
        )
        for source, tally in tallies.items()
        if source != "all" and SOURCE_ID.fullmatch(source)
    )
    return tuple(sorted(usage, key=lambda item: (-item.total_tokens, item.source)))
