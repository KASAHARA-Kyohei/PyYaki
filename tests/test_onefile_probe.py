import json

from onefile_windows import owned_records, phase_order


def test_shared_cache_logs_cannot_select_another_launchers_host(tmp_path):
    def record(name, filename, prefix, pid):
        folder = tmp_path / name
        folder.mkdir()
        (folder / filename).write_text(
            prefix + json.dumps({"phase": "started", "data": {"pid": pid}}) + "\n",
            encoding="utf-8",
        )
        return folder

    own = record("own", "launcher.log", "pyyaki_onefile=", 100)
    child = record("child", "host.log", "pyyaki_startup=", 101)
    foreign = record("foreign", "launcher.log", "pyyaki_onefile=", 200)
    foreign_child = record("foreign-child", "host.log", "pyyaki_startup=", 201)
    result = owned_records({own, child, foreign, foreign_child}, 100, {100: {}, 101: {}})
    assert {item["instance"] for item in result} == {str(own), str(child)}
    assert not phase_order([], "launcher_native_draw", "preparation_started")
