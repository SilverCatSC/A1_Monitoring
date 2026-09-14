"""Add conservative Vehicle/Offer identity without rewriting monitoring history."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '20260914_0008'
down_revision = '20260914_0007'
branch_labels = None
depends_on = None


def _source_type(bind):
    if bind.dialect.name == 'postgresql':
        return postgresql.ENUM('AUTO_RU', 'AVITO', name='enginetype', create_type=False)
    return sa.Enum('AUTO_RU', 'AVITO', name='enginetype')


def _column_names(bind, table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    return {index['name'] for index in sa.inspect(bind).get_indexes(table_name)}


def _vin_fingerprint(value: object) -> str | None:
    normalized = str(value or '').strip().upper()
    if not normalized or normalized == 'НЕ УКАЗАН':
        return None
    return hashlib.sha256(f'vin|{normalized}'.encode('utf-8')).hexdigest()


def _offer_key(source: str, value: object) -> str | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = urlparse(raw if '://' in raw else f'https://{raw.lstrip("/")}')
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = unquote(parsed.path).rstrip('/').lower()
    if source == 'AUTO_RU':
        if not (host == 'auto.ru' or host.endswith('.auto.ru')):
            return None
        for segment in reversed(path.split('/')):
            match = re.match(r'^(\d{5,})(?:-|$)', segment)
            if match:
                return f'auto_ru:{match.group(1)}'
        identifiers = re.findall(r'(?<!\d)(\d{5,})(?!\d)', path)
        return f'auto_ru:{identifiers[-1]}' if identifiers else None
    if source == 'AVITO':
        if not (host == 'avito.ru' or host.endswith('.avito.ru')):
            return None
        match = re.search(r'_(\d{5,})(?:$|/)', path)
        if match:
            return f'avito:{match.group(1)}'
        identifiers = re.findall(r'(?<!\d)(\d{5,})(?!\d)', path)
        return f'avito:{identifiers[-1]}' if identifiers else None
    return None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    source_type = _source_type(bind)

    if 'vehicles' not in tables:
        op.create_table(
            'vehicles',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('vin_fingerprint', sa.String()),
            sa.Column('identity_state', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if 'ix_vehicles_vin_fingerprint' not in _index_names(bind, 'vehicles'):
        op.create_index('ix_vehicles_vin_fingerprint', 'vehicles', ['vin_fingerprint'])

    if 'vehicle_id' not in _column_names(bind, 'listings'):
        # SQLite cannot safely add a foreign-key constraint to an existing table.
        # The ORM relation is retained, while the data migration gives every legacy
        # projection a Vehicle before the unique index is created.
        op.add_column('listings', sa.Column('vehicle_id', sa.String(), nullable=True))

    if 'offers' not in tables:
        op.create_table(
            'offers',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('source', source_type, nullable=False),
            sa.Column('external_key', sa.String(), nullable=False),
            sa.Column('current_url', sa.Text(), nullable=False),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('source', 'external_key', name='uq_offer_source_external_key'),
        )
    if 'ix_offers_source' not in _index_names(bind, 'offers'):
        op.create_index('ix_offers_source', 'offers', ['source'])
    if 'ix_offers_active' not in _index_names(bind, 'offers'):
        op.create_index('ix_offers_active', 'offers', ['active'])

    if 'source_records' not in tables:
        op.create_table(
            'source_records',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('snapshot_id', sa.String(), sa.ForeignKey('source_import_snapshots.id'), nullable=False),
            sa.Column('row_number', sa.Integer(), nullable=False),
            sa.Column('row_fingerprint', sa.String(), nullable=False),
            sa.Column('vin_fingerprint', sa.String()),
            sa.Column('offer_keys', sa.JSON(), nullable=False),
            sa.Column('vehicle_id', sa.String(), sa.ForeignKey('vehicles.id')),
            sa.Column('listing_id', sa.String(), sa.ForeignKey('listings.id')),
            sa.Column('resolution', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('snapshot_id', 'row_number', name='uq_source_record_snapshot_row'),
        )
    for column in ('snapshot_id', 'vin_fingerprint', 'vehicle_id', 'listing_id'):
        index_name = f'ix_source_records_{column}'
        if index_name not in _index_names(bind, 'source_records'):
            op.create_index(index_name, 'source_records', [column])

    if 'offer_vehicle_links' not in tables:
        op.create_table(
            'offer_vehicle_links',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('offer_id', sa.String(), sa.ForeignKey('offers.id'), nullable=False),
            sa.Column('vehicle_id', sa.String(), sa.ForeignKey('vehicles.id'), nullable=False),
            sa.Column('source_record_id', sa.String(), sa.ForeignKey('source_records.id')),
            sa.Column('state', sa.String(), nullable=False),
            sa.Column('method', sa.String(), nullable=False),
            sa.Column('actor', sa.String()),
            sa.Column('reason', sa.Text()),
            sa.Column('details', sa.JSON()),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('closed_at', sa.DateTime(timezone=True)),
        )
    for index_name, columns in (
        ('ix_offer_vehicle_links_offer_id', ['offer_id']),
        ('ix_offer_vehicle_links_vehicle_id', ['vehicle_id']),
        ('ix_offer_vehicle_links_source_record_id', ['source_record_id']),
        ('ix_offer_vehicle_links_state', ['state']),
        ('ix_offer_vehicle_link_offer_state', ['offer_id', 'state']),
        ('ix_offer_vehicle_link_vehicle_state', ['vehicle_id', 'state']),
    ):
        if index_name not in _index_names(bind, 'offer_vehicle_links'):
            op.create_index(index_name, 'offer_vehicle_links', columns)

    now = datetime.now(UTC)
    listing_rows = bind.execute(
        sa.text(
            'SELECT id, vehicle_id, vin, source_auto_ru, source_avito '
            'FROM listings ORDER BY id'
        )
    ).mappings().all()
    vehicle_rows: list[dict[str, object]] = []
    update_rows: list[dict[str, str]] = []
    listing_vehicle_ids: dict[str, str] = {}
    for row in listing_rows:
        vehicle_id = row['vehicle_id'] or str(uuid.uuid4())
        listing_vehicle_ids[row['id']] = vehicle_id
        if not row['vehicle_id']:
            vehicle_rows.append(
                {
                    'id': vehicle_id,
                    'vin_fingerprint': _vin_fingerprint(row['vin']),
                    'identity_state': 'legacy_backfill',
                    'created_at': now,
                    'updated_at': now,
                }
            )
            update_rows.append({'id': row['id'], 'vehicle_id': vehicle_id})
    if vehicle_rows:
        bind.execute(
            sa.table(
                'vehicles',
                sa.column('id', sa.String()),
                sa.column('vin_fingerprint', sa.String()),
                sa.column('identity_state', sa.String()),
                sa.column('created_at', sa.DateTime(timezone=True)),
                sa.column('updated_at', sa.DateTime(timezone=True)),
            ).insert(),
            vehicle_rows,
        )
        bind.execute(
            sa.text('UPDATE listings SET vehicle_id = :vehicle_id WHERE id = :id'),
            update_rows,
        )

    existing_offers = {
        (row['source'], row['external_key']): row['id']
        for row in bind.execute(sa.text('SELECT id, source, external_key FROM offers')).mappings()
    }
    offer_rows: list[dict[str, object]] = []
    link_rows: list[dict[str, object]] = []
    for row in listing_rows:
        for source, column in (('AUTO_RU', 'source_auto_ru'), ('AVITO', 'source_avito')):
            url = row[column]
            key = _offer_key(source, url)
            if key is None:
                continue
            offer_id = existing_offers.get((source, key))
            if offer_id is None:
                offer_id = str(uuid.uuid4())
                existing_offers[(source, key)] = offer_id
                offer_rows.append(
                    {
                        'id': offer_id,
                        'source': source,
                        'external_key': key,
                        'current_url': url,
                        'active': True,
                        'first_seen_at': now,
                        'last_seen_at': now,
                    }
                )
            link_rows.append(
                {
                    'id': str(uuid.uuid4()),
                    'offer_id': offer_id,
                    'vehicle_id': listing_vehicle_ids[row['id']],
                    'state': 'candidate',
                    'method': 'legacy_backfill',
                    'created_at': now,
                }
            )
    if offer_rows:
        bind.execute(
            sa.table(
                'offers',
                sa.column('id', sa.String()),
                sa.column('source', source_type),
                sa.column('external_key', sa.String()),
                sa.column('current_url', sa.Text()),
                sa.column('active', sa.Boolean()),
                sa.column('first_seen_at', sa.DateTime(timezone=True)),
                sa.column('last_seen_at', sa.DateTime(timezone=True)),
            ).insert(),
            offer_rows,
        )
    if link_rows:
        bind.execute(
            sa.table(
                'offer_vehicle_links',
                sa.column('id', sa.String()),
                sa.column('offer_id', sa.String()),
                sa.column('vehicle_id', sa.String()),
                sa.column('state', sa.String()),
                sa.column('method', sa.String()),
                sa.column('created_at', sa.DateTime(timezone=True)),
            ).insert(),
            link_rows,
        )

    if 'ix_listings_vehicle_id' not in _index_names(bind, 'listings'):
        op.create_index('ix_listings_vehicle_id', 'listings', ['vehicle_id'], unique=True)


def downgrade() -> None:
    raise RuntimeError(
        'Stable identity is append-only. Restore a verified backup rather than deleting identity history.'
    )
