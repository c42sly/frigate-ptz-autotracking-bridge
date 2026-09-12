"""Tests for the scan-findings fixes (#1, #4, #5, #6, #10)."""
import os
import sys
import time
import queue
import subprocess
import tempfile
import importlib.util
import app as bridge

HERE = os.path.dirname(os.path.abspath(__file__))


def test_to_float():  # #6
    assert bridge._to_float("0.25") == 0.25
    assert bridge._to_float(None) == 0.0
    assert bridge._to_float("garbage") == 0.0
    assert bridge._to_float("garbage", -1.0) == -1.0
    print("PASS  #6  _to_float tolerates missing/garbage")


def test_dispatch_coalesce():  # #10
    eng = bridge.MotionEngine("t", {"ip": "10.0.0.9", "user": "admin"}, "p")
    eng.dispatch_move("RELATIVE", 0.1, 0.0)
    eng.dispatch_move("RELATIVE", 0.2, 0.0)
    eng.dispatch_move("RELATIVE", 0.3, 0.0)
    assert eng.work_queue.qsize() == 1, eng.work_queue.qsize()
    mt, x, y = eng.work_queue.get_nowait()
    assert (mt, x) == ("RELATIVE", 0.3)
    print("PASS  #10 dispatch_move coalesces stale relative moves")


def test_http_port():  # #7
    eng = bridge.MotionEngine("t", {"ip": "10.0.0.9", "user": "admin", "http_port": 8000}, "p")
    assert eng.base_url == "http://10.0.0.9:8000/form/setPTZCfg", eng.base_url
    dflt = bridge.MotionEngine("t", {"ip": "10.0.0.9", "user": "admin"}, "p")
    assert dflt.base_url == "http://10.0.0.9:80/form/setPTZCfg", dflt.base_url
    print("PASS  #7  configurable http_port (default 80)")


def test_odm_force_stop_drains():  # #1
    path = os.path.join(HERE, "tools", "odm_mock_validator.py")
    spec = importlib.util.spec_from_file_location("odm_mock_validator", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = mod.MotionEngine("t", {"ip": "10.0.0.9", "user": "admin"}, "p")
    eng._send_http_cmd = lambda d: None  # avoid network
    for _ in range(5):
        eng.work_queue.put(("RELATIVE", 0.1, 0.0, 0.0))
    assert eng.work_queue.qsize() == 5
    eng.force_stop()
    assert eng.work_queue.qsize() == 0, eng.work_queue.qsize()
    print("PASS  #1  odm force_stop actually drains the queue")


def _run_app(tmpdir, env, seconds=2.5):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app.py")],
        cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    time.sleep(seconds)
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out


def _write_cfg(tmpdir, password_env, port):
    with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
        f.write(
            "cameras:\n"
            "  cam2:\n"
            "    ip: '10.0.0.9'\n"
            "    user: admin\n"
            f"    password_env: {password_env}\n"
            f"    port: {port}\n"
        )


def test_missing_env_skips():  # #5
    with tempfile.TemporaryDirectory() as td:
        _write_cfg(td, "NOPE_MISSING_VAR", 9997)
        env = {k: v for k, v in os.environ.items() if k != "NOPE_MISSING_VAR"}
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        out = _run_app(td, env)
    assert "WARNING" in out and "skipping" in out, out
    assert "Started Proxy" not in out, out
    print("PASS  #5  missing password_env -> warn + skip (no silent 'admin')")


def test_present_env_starts_no_utf8():  # #5 happy path + #4 emoji safety
    with tempfile.TemporaryDirectory() as td:
        _write_cfg(td, "CAM_X_PASS", 9996)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        env["CAM_X_PASS"] = "secret"          # deliberately NOT utf-8-forcing the child
        env["PYTHONUNBUFFERED"] = "1"         # so we can read the child's stdout
        out = _run_app(td, env)
    # If the emoji print crashed the thread (pre-fix), the server never starts.
    assert "Started Proxy" in out, out
    print("PASS  #4  emoji log line survives non-UTF-8 stdout; proxy starts (#5 happy path)")


if __name__ == "__main__":
    test_to_float()
    test_dispatch_coalesce()
    test_http_port()
    test_odm_force_stop_drains()
    test_missing_env_skips()
    test_present_env_starts_no_utf8()
    print("\nALL FIX TESTS PASSED")
