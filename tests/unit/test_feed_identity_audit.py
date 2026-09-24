from app.service.feed_identity_audit import audit_autoru_identity_rows, audit_avito_identity_rows


def test_autoru_unique_id_is_a_valid_placement_id():
    audit = audit_autoru_identity_rows(
        [{'unique_id': 'MBVC011220262508260027'}], first_data_row=3
    )

    assert audit.is_ready
    assert audit.placement_ids == ('MBVC011220262508260027',)
    assert audit.findings == ()


def test_avito_keeps_customer_placement_and_platform_ids_separate():
    audit = audit_avito_identity_rows(
        [{'Id': 'MBVC011220262508260027', 'AvitoId': '8080447555'}], first_data_row=4
    )

    assert audit.is_ready
    assert audit.placement_ids == ('MBVC011220262508260027',)


def test_avito_platform_id_cannot_replace_customer_placement_id():
    audit = audit_avito_identity_rows(
        [{'Id': '7888801666', 'AvitoId': '7888801666'}], first_data_row=4
    )

    assert not audit.is_ready
    assert {(finding.code, finding.row_number) for finding in audit.findings} == {
        ('invalid_placement_id', 4),
        ('avito_id_reused_as_placement_id', 4),
    }


def test_duplicate_placement_id_fails_closed_with_both_row_numbers():
    audit = audit_autoru_identity_rows(
        [
            {'unique_id': 'MBVC011220262508260027'},
            {'unique_id': 'MBVC011220262508260027'},
        ]
    )

    assert not audit.is_ready
    assert {(finding.code, finding.row_number) for finding in audit.findings} == {
        ('duplicate_placement_id', 3),
        ('duplicate_placement_id', 4),
    }


def test_blank_sheet_rows_are_skipped_without_losing_source_row_numbers():
    audit = audit_autoru_identity_rows([
        {'unique_id': ''},
        {'unique_id': 'MBVC011220262508260027'},
    ])

    assert audit.is_ready
    assert audit.checked_rows == 1


def test_empty_feed_fails_closed():
    audit = audit_autoru_identity_rows([{'unique_id': ''}])

    assert not audit.is_ready
    assert audit.findings[0].code == 'empty_feed'
