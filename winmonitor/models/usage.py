"""The normalized AI usage model the AI Usage view renders.

Usage tools disagree on field names (ccusage alone has used ``totalCost``,
``costUSD`` and ``cost``, and ``date``, ``week`` or ``period`` for the same
column).  A provider's normalizer (see :mod:`winmonitor.providers`) maps its
tool's JSON onto these models once, so the table, the totals line and the
BY SOURCE summary never read a tool-specific key.

``None`` always means "the tool did not report this", never zero: the table
shows a column only when at least one row supplied it, and an unknown cost
must not be summed as $0.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ExtraValue", "TokenCounts", "UsageReport", "UsageRow"]

#: A field the normalizer does not know but that is still shown as a column.
ExtraValue = str | int | float | bool | None


class TokenCounts(BaseModel):
    """Token counts by kind; ``None`` where the tool did not report that kind."""

    model_config = ConfigDict(frozen=True)

    input: int | None = None
    output: int | None = None
    cache_creation: int | None = None
    cache_read: int | None = None
    total: int | None = None


class UsageRow(BaseModel):
    """One report row: a day, week, month or session, for one or all sources."""

    model_config = ConfigDict(frozen=True)

    #: The row's date, week, month or session ID, as the tool wrote it.
    period: str | None = None
    #: The coding agent this row belongs to (``claude``, ``codex``, ...).
    source: str | None = None
    #: True for a row the tool combined across every source.
    all_sources: bool = False
    project: str | None = None
    models: tuple[str, ...] | None = None
    tokens: TokenCounts = Field(default_factory=TokenCounts)
    #: Estimated cost in US dollars.
    cost: float | None = None
    first_activity: str | None = None
    last_activity: str | None = None
    #: The per-source split of an ``all_sources`` row, when the tool gave one.
    by_source: tuple[UsageRow, ...] = ()
    #: Scalar fields the normalizer does not know, by their original name, so
    #: a newer tool version's additions stay visible.
    extra: Mapping[str, ExtraValue] = Field(default_factory=dict)


class UsageReport(BaseModel):
    """A whole normalized report plus the tool's own grand totals."""

    model_config = ConfigDict(frozen=True)

    #: ``daily``, ``weekly``, ``monthly`` or ``session``.
    report_type: str
    #: The source filter the report was requested with; ``None`` for all.
    source: str | None
    #: The tool that produced the report, e.g. ``ccusage``.
    provider: str
    rows: tuple[UsageRow, ...] = ()
    totals: TokenCounts = Field(default_factory=TokenCounts)
    total_cost: float | None = None
