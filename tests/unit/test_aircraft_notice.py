from app.services.catalog_service import returned_to_service

MAINTENANCE = '{"status":"maintenance","expected_return":"2026-12-01"}'
OPERATIONAL = '{"status":"operational","expected_return":""}'


def test_a_blank_sheet_filled_as_operational_is_not_a_return():
    assert returned_to_service(None, OPERATIONAL) is False
    assert returned_to_service("", OPERATIONAL) is False


def test_leaving_maintenance_for_operational_is_a_return():
    assert returned_to_service(MAINTENANCE, OPERATIONAL) is True


def test_staying_operational_or_staying_in_maintenance_is_not_a_return():
    assert returned_to_service(OPERATIONAL, OPERATIONAL) is False
    assert returned_to_service(MAINTENANCE, MAINTENANCE) is False
    assert returned_to_service(OPERATIONAL, MAINTENANCE) is False
