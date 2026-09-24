"""Fail-closed identity checks at the outbound marketplace-feed boundary.

The check deliberately validates only the identity columns.  It does not send a
feed, alter a marketplace URL or decide whether two placements are the same
physical vehicle.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.service.placement_identity import PlacementIdError, parse_placement_id


@dataclass(frozen=True)
class FeedIdentityFinding:
    """One actionable failure in a feed's identity columns."""

    row_number: int
    code: str
    message: str


@dataclass(frozen=True)
class FeedIdentityAudit:
    """Immutable outcome suitable for a pre-publication gate."""

    platform: str
    checked_rows: int
    placement_ids: tuple[str, ...]
    findings: tuple[FeedIdentityFinding, ...]

    @property
    def is_ready(self) -> bool:
        """Only an audit with no identity failures may cross the feed boundary."""
        return not self.findings


def audit_autoru_identity_rows(
    rows: Iterable[Mapping[str, object]], *, first_data_row: int = 3
) -> FeedIdentityAudit:
    """Validate Auto.ru's ``unique_id`` placement field before export."""
    return _audit_placement_rows(
        platform='auto_ru', rows=rows, placement_field='unique_id', first_data_row=first_data_row
    )


def audit_avito_identity_rows(
    rows: Iterable[Mapping[str, object]], *, first_data_row: int = 2
) -> FeedIdentityAudit:
    """Validate Avito's customer ``Id`` separately from platform ``AvitoId``.

    ``AvitoId`` is a platform-generated listing identifier.  It may be blank on
    a newly exported item, but it cannot replace the customer placement ID in
    ``Id``.  Keeping the two columns separate makes a relisting traceable.
    """
    materialized_rows = list(rows)
    audit = _audit_placement_rows(
        platform='avito',
        rows=materialized_rows,
        placement_field='Id',
        first_data_row=first_data_row,
    )
    findings = list(audit.findings)
    for offset, row in enumerate(materialized_rows, start=first_data_row):
        if not _has_values(row):
            continue
        placement_id = _text(row.get('Id'))
        platform_id = _text(row.get('AvitoId'))
        if placement_id and platform_id and placement_id == platform_id:
            findings.append(
                FeedIdentityFinding(
                    row_number=offset,
                    code='avito_id_reused_as_placement_id',
                    message='Id must contain the A1 placement ID; AvitoId belongs only in AvitoId',
                )
            )
    return FeedIdentityAudit(
        platform=audit.platform,
        checked_rows=audit.checked_rows,
        placement_ids=audit.placement_ids,
        findings=tuple(findings),
    )


def _audit_placement_rows(
    *,
    platform: str,
    rows: Iterable[Mapping[str, object]],
    placement_field: str,
    first_data_row: int,
) -> FeedIdentityAudit:
    materialized_rows = list(rows)
    findings: list[FeedIdentityFinding] = []
    parsed_ids: list[tuple[int, str]] = []
    checked_rows = 0

    for offset, row in enumerate(materialized_rows, start=first_data_row):
        if not _has_values(row):
            continue
        checked_rows += 1
        value = _text(row.get(placement_field))
        if not value:
            findings.append(
                FeedIdentityFinding(
                    row_number=offset,
                    code='missing_placement_id',
                    message=f'{placement_field} must contain a 22-character A1 placement ID',
                )
            )
            continue
        try:
            parsed_ids.append((offset, parse_placement_id(value).value))
        except PlacementIdError as exc:
            findings.append(
                FeedIdentityFinding(
                    row_number=offset,
                    code='invalid_placement_id',
                    message=f'{placement_field}: {exc}',
                )
            )

    if checked_rows == 0:
        findings.append(FeedIdentityFinding(
            row_number=first_data_row,
            code='empty_feed',
            message=f'{platform} feed has no publication rows to audit',
        ))

    occurrences = Counter(value for _, value in parsed_ids)
    for row_number, value in parsed_ids:
        if occurrences[value] > 1:
            findings.append(
                FeedIdentityFinding(
                    row_number=row_number,
                    code='duplicate_placement_id',
                    message=f'{placement_field} repeats {value} within one {platform} feed',
                )
            )

    return FeedIdentityAudit(
        platform=platform,
        checked_rows=checked_rows,
        placement_ids=tuple(value for _, value in parsed_ids),
        findings=tuple(findings),
    )


def _text(value: object | None) -> str:
    return str(value).strip() if value is not None else ''


def _has_values(row: Mapping[str, object]) -> bool:
    return any(_text(value) for value in row.values())
