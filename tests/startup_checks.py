"""Evidence checks shared by native maintainer probes; no GUI or process side effects."""

PHASES = (
    "shell_marker",
    "python_spawn_started",
    "python_spawned",
    "worker_entered",
    "config_loaded",
    "app_import_started",
    "app_imported",
    "startup_hooks_started",
    "startup_hooks_completed",
    "worker_welcome",
    "sdk_welcome_posted",
)
FAILURES = {"worker_failed", "backend_failed", "sdk_connect_timeout", "host_initialization_timeout"}


def validate_space_restore(trace):
    errors = []
    changes = [
        (i, row.get("data", {}))
        for i, row in enumerate(trace)
        if row.get("phase") == "macos_initial_space_behavior"
    ]
    if len(changes) != 2:
        return ["Expected exactly one Space preparation and one restoration"]
    (prepared_at, prepared), (restored_at, restored) = changes
    original = prepared.get("original_bits")
    applied = prepared.get("applied_bits")
    if prepared.get("action") != "applied" or restored.get("action") != "restored":
        errors.append("Invalid Space preparation/restoration actions")
    if type(original) is not int or type(applied) is not int or original < 0 or applied < 0:
        errors.append("Invalid Space behavior bits")
    elif (
        not applied & 2
        or applied & 1
        or (applied & ~3) != (original & ~3)
        or restored.get("restored_bits") != original
    ):
        errors.append("Space behavior was not preserved and restored")
    phases = [row.get("phase") for row in trace]
    if "shell_marker" not in phases or "python_spawn_started" not in phases:
        errors.append("Cannot verify Space restoration before Python")
    elif (
        not prepared_at
        < phases.index("shell_marker")
        < restored_at
        < phases.index("python_spawn_started")
    ):
        errors.append("Space restoration did not occur between shell and Python")
    if restored.get("reason") != "shell":
        errors.append("Successful startup must restore Space behavior at shell")
    return errors


def validate_startup(trace, *, require_space_restore=False):
    phases = [row.get("phase") for row in trace]
    errors = []
    if any(phases.count(phase) != 1 for phase in PHASES):
        errors.append("Missing or repeated required startup phase")
    elif [phases.index(phase) for phase in PHASES] != sorted(
        phases.index(phase) for phase in PHASES
    ):
        errors.append("Invalid shell/Python/worker/welcome order")
    frames = [
        (i, row.get("data", {}).get("stage"))
        for i, row in enumerate(trace)
        if row.get("phase") == "sdk_shell_progress"
        and row.get("data", {}).get("stage") in {"scheduled", "frame1", "frame2"}
    ]
    if [stage for _, stage in frames] != ["scheduled", "frame1", "frame2"]:
        errors.append("Missing or repeated double-rAF progress")
    elif "shell_marker" not in phases or frames[-1][0] >= phases.index("shell_marker"):
        errors.append("Shell preceded the second animation frame")
    for phase in ("ui_deadline_started", "backend_deadline_started"):
        rows = [row for row in trace if row.get("phase") == phase]
        if len(rows) != 1 or rows[0].get("data", {}).get("timeout_ms") != 10000:
            errors.append(f"Invalid 10,000 ms deadline: {phase}")
    if FAILURES.intersection(phases):
        errors.append("Startup contains a backend failure or timeout")
    if require_space_restore:
        errors.extend(validate_space_restore(trace))
    return errors
