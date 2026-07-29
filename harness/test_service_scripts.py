from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_pid_files_are_ignored():
    ignore = _read(".gitignore")
    assert ".backend.pid" in ignore
    assert ".frontend.pid" in ignore


def test_unix_scripts_are_executable():
    assert (ROOT / "start.sh").stat().st_mode & 0o111
    assert (ROOT / "stop.sh").stat().st_mode & 0o111


def test_unix_start_requires_supported_runtime_versions():
    script = _read("start.sh")
    assert "Python 3.12+" in script
    assert "Node.js 18+" in script
    assert "check_version" in script


def test_unix_start_is_idempotent_and_persistent():
    script = _read("start.sh")
    assert "process_belongs_to_project" in script
    assert "port_pid" in script
    assert "nohup" in script
    assert "already running" in script.lower()


def test_unix_start_rolls_back_on_failed_health_check():
    script = _read("start.sh")
    assert "wait_for_url" in script
    assert "stop_started_services" in script
    assert "exit 1" in script


def test_unix_stop_never_kills_an_unverified_port_owner():
    script = _read("stop.sh")
    assert "process_belongs_to_project" in script
    assert "lsof -ti \":$port\"" not in script
    assert "kill -9" not in script


def test_windows_scripts_use_pid_files_and_custom_ports():
    start = _read("start.ps1")
    stop = _read("stop.ps1")
    for script in (start, stop):
        assert "BACKEND_PORT" in script
        assert "FRONTEND_PORT" in script
        assert ".backend.pid" in script
        assert ".frontend.pid" in script


def test_windows_stop_validates_project_command_line():
    script = _read("stop.ps1")
    assert "Win32_Process" in script
    assert "CommandLine" in script
    assert "taskkill /F /PID" not in script


def test_scripts_do_not_publish_stale_project_versions():
    for name in ("start.sh", "stop.sh", "start.bat", "stop.bat", "start.ps1", "stop.ps1"):
        text = _read(name)
        assert "v2.3.1" not in text
