from macos_host_cleanup import same_process


def test_owned_identity_allows_reparenting_but_rejects_replacement():
    first = {"started": "one", "command": "own project", "pgid": 123, "state": "S", "ppid": 10}
    assert same_process(first, {**first, "ppid": 1})
    assert same_process(first, {**first, "state": "Z", "command": "(Python)"})
    assert not same_process(first, None)
    assert not same_process(first, {**first, "started": "later"})
    assert not same_process(first, {**first, "command": "another project"})
    assert not same_process(first, {**first, "pgid": 456})
