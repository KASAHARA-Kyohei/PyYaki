"""Report math only; does not launch macOS windows or power assertions."""

from diagnostics_macos import owns_worker, timer_summary


def test_fault_target_requires_owned_direct_or_logged_guard_chain():
    table = {10: {"ppid": 1}, 20: {"ppid": 10}, 30: {"ppid": 20}, 40: {"ppid": 1}}
    markers = [
        {
            "phase": "python_spawned",
            "data": {"worker_pid": 30, "guard_pid": 20, "process_group": 20},
        }
    ]
    assert owns_worker(table, 10, 20, [])
    assert owns_worker(table, 10, 30, markers)
    assert not owns_worker(table, 10, 30, [])
    assert not owns_worker(table, 40, 30, markers)
    assert not owns_worker(table, 10, 40, markers)


def record():
    return {
        "result": {
            "segments": [
                {
                    "index": 0,
                    "name": "foreground",
                    "duration_ms": 4000,
                    "native_start_ms": 1000,
                    "rpc_ms": [3, 4],
                    "crossing_rpcs": 4,
                    "timer_samples": [
                        {
                            "elapsed_ms": 1000,
                            "lag_ms": 800,
                            "visibility": "hidden",
                            "focused": False,
                        },
                        {"elapsed_ms": 2000, "lag_ms": 5, "visibility": "visible", "focused": True},
                        {"elapsed_ms": 3000, "lag_ms": 7, "visibility": "visible", "focused": True},
                    ],
                }
            ]
        },
        "markers": [
            {
                "phase": "timer_native_segment",
                "data": {
                    "index": 0,
                    "samples": [
                        {
                            "elapsed_ms": 1000,
                            "lag_ms": 600,
                            "visible": False,
                            "focused": False,
                            "minimized": True,
                        },
                        {
                            "elapsed_ms": 2000,
                            "lag_ms": 3,
                            "visible": True,
                            "focused": True,
                            "minimized": False,
                        },
                        {
                            "elapsed_ms": 3000,
                            "lag_ms": 4,
                            "visible": True,
                            "focused": True,
                            "minimized": False,
                        },
                    ],
                },
            },
            {"phase": "timer_phase_seen", "host_elapsed_ms": 1010, "data": {"index": 0}},
        ],
    }


def test_transition_and_settled_samples_are_kept_separate():
    summary = timer_summary(record())[0]
    assert summary["js_lag_ms"] == {"n": 2, "p50": 5, "p95": 7, "max": 7}
    assert summary["raw_js_lag_ms"]["max"] == 800
    assert summary["native_lag_ms"] == {"n": 2, "p50": 3, "p95": 4, "max": 4}
    assert summary["raw_native_lag_ms"]["max"] == 600
    assert summary["dom_states"] == [{"visibility": "visible", "focused": True, "n": 2}]
    assert summary["native_states"] == [
        {"visible": True, "focused": True, "minimized": False, "n": 2}
    ]
    assert summary["crossing_rpcs"] == 4 and summary["rpc_ms"]["n"] == 2
    assert summary["phase_ack_ms"] == 10
    assert summary["native_state_matches"] is True


def test_missing_steady_samples_are_not_reported_as_zero_lag():
    data = record()
    data["result"]["segments"][0]["timer_samples"] = []
    data["markers"][0]["data"]["samples"] = []
    summary = timer_summary(data)[0]
    assert summary["js_lag_ms"] is None
    assert summary["native_lag_ms"] is None
    assert summary["dom_states"] == [] and summary["native_states"] == []
    assert summary["native_state_matches"] is False


def test_unexpected_window_state_invalidates_the_segment():
    data = record()
    data["markers"][0]["data"]["samples"][2].update(visible=False, minimized=True)
    assert timer_summary(data)[0]["native_state_matches"] is False
