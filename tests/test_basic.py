"""Basic tests: extractor, cache, CLI idempotency."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def make_sample(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(
        '"""Sample module."""\n'
        "API_KEY = 'x'\n\n"
        "def hello(name):\n"
        '    """Greets a user."""\n'
        "    return f'hi {name}'\n"
    )
    return f


def test_skeleton_keeps_signatures_drops_bodies(tmp_path):
    os.environ["PROJMAP_ROOT"] = str(tmp_path)
    from projmap import server
    f = make_sample(tmp_path)
    skel = server._skeleton(f)
    assert "def hello(name)" in skel
    assert "Greets a user" in skel
    assert "const API_KEY" in skel
    assert "return" not in skel  # tana tashlangan


def test_refresh_cache_hit(tmp_path, monkeypatch):
    from projmap import server
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "CACHE_PATH", tmp_path / ".projmap_cache.json")
    make_sample(tmp_path)
    first = server._refresh()
    second = server._refresh()
    assert first == second
    assert "sample.py" in second


def test_cli_init_idempotent(tmp_path):
    make_sample(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")}
    for _ in range(2):
        r = subprocess.run(
            [sys.executable, "-m", "projmap.cli", "init"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
    claude_md = (tmp_path / "CLAUDE.md").read_text()
    assert claude_md.count("projmap (avtomatik") == 1
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "projmap" in cfg["mcpServers"]
