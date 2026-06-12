"""momentum_fast_exit_enabled gates hour-window momentum-stop-loss."""

from config.settings import RuntimeSettings, default_runtime_dict
from strategy.decision_core import momentum_fast_exit_window_enabled


def test_window_exit_disabled_by_default():
    d = default_runtime_dict()
    s = RuntimeSettings.from_dict(d)
    assert momentum_fast_exit_window_enabled(s) is False


def test_window_exit_disabled_via_runtime():
    d = default_runtime_dict()
    d["momentum_fast_exit_enabled"] = False
    s = RuntimeSettings.from_dict(d)
    assert momentum_fast_exit_window_enabled(s) is False
