"""行业板块排名的来源身份与行合同。"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


class SectorRankingContractError(ValueError):
    """来源响应无法安全映射为行业排名。"""


def normalize_sector_rankings(
    frame: Any,
    *,
    name_column: str,
    change_column: str,
    source: str,
    n: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]] | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    if not isinstance(name_column, str) or not isinstance(change_column, str):
        raise SectorRankingContractError("invalid_sector_columns")
    if name_column not in frame.columns or change_column not in frame.columns:
        raise SectorRankingContractError("missing_sector_columns")
    if type(n) is not int or n < 1:
        raise SectorRankingContractError("invalid_sector_limit")

    rows = frame[[name_column, change_column]].copy()
    names: list[str] = []
    for value in rows[name_column].tolist():
        if not isinstance(value, str) or not value.strip():
            raise SectorRankingContractError("invalid_sector_identity")
        names.append(value.strip())
    if len(names) != len(set(names)):
        raise SectorRankingContractError("duplicate_sector_identity")
    rows[name_column] = names

    rows[change_column] = pd.to_numeric(rows[change_column], errors="coerce")
    rows = rows[rows[change_column].map(lambda value: bool(math.isfinite(value)))]
    if rows.empty:
        raise SectorRankingContractError("missing_sector_change")

    def serialize(data: pd.DataFrame) -> list[dict[str, object]]:
        return [
            {
                "name": str(row[name_column]),
                "change_pct": float(row[change_column]),
                "source": source,
            }
            for _, row in data.iterrows()
        ]

    top = rows.nlargest(n, change_column)
    bottom = rows.nsmallest(n, change_column)
    return serialize(top), serialize(bottom)
