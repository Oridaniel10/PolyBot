from strategy.city_tz import format_hour_float_as_hhmm


def test_format_hour_float_whole_hour():
    assert format_hour_float_as_hhmm(15.0) == "15:00"


def test_format_hour_float_half_hour():
    assert format_hour_float_as_hhmm(15.5) == "15:30"


def test_format_hour_float_quarter_hour():
    assert format_hour_float_as_hhmm(15.25) == "15:15"
    assert format_hour_float_as_hhmm(16.75) == "16:45"
