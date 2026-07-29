from datetime import datetime, timezone

from km.timeutil import infer_timestamp, usec_to_dt, webkit_to_dt


def test_webkit_epoch_conversion():
    # 13217370610000000 us after 1601-01-01 is 2019-11-04 19:50:10 UTC
    # (cross-checked against Unix epoch offset 11644473600 s)
    dt = webkit_to_dt(13217370610000000)
    assert dt == datetime(2019, 11, 4, 19, 50, 10, tzinfo=timezone.utc)


def test_webkit_zero_is_1601():
    assert webkit_to_dt(0) == datetime(1601, 1, 1, tzinfo=timezone.utc)


def test_usec_to_dt():
    # 1700000000000000 usec after Unix epoch is 2023-11-14 22:13:20 UTC
    dt = usec_to_dt(1700000000000000)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_infer_seconds():
    dt = infer_timestamp(1700000000)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_infer_millis():
    dt = infer_timestamp(1700000000000)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_infer_micros_unix():
    dt = infer_timestamp(1700000000000000)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_infer_webkit_micros():
    dt = infer_timestamp(13217370610000000)
    assert dt == datetime(2019, 11, 4, 19, 50, 10, tzinfo=timezone.utc)


def test_infer_iso_string():
    dt = infer_timestamp("2024-05-01T12:30:00Z")
    assert dt == datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)


def test_infer_iso_date_only():
    dt = infer_timestamp("2024-05-01")
    assert dt == datetime(2024, 5, 1, tzinfo=timezone.utc)


def test_infer_common_us_format():
    dt = infer_timestamp("5/1/2024 12:30:00 PM")
    assert dt == datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)


def test_infer_numeric_string():
    dt = infer_timestamp("1700000000")
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_infer_garbage_returns_none():
    assert infer_timestamp("not a date") is None
    assert infer_timestamp(None) is None
    assert infer_timestamp("") is None


def test_twitter_created_at_format():
    dt = infer_timestamp("Wed Oct 10 20:19:24 +0000 2018")
    assert dt == datetime(2018, 10, 10, 20, 19, 24, tzinfo=timezone.utc)
