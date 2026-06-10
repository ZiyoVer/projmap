"""Tests: extractors (Python + JS/TS), cache, symbol search, CLI idempotency."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from projmap import core  # noqa: E402

PY_SAMPLE = '''"""Sample module."""
from typing import Optional

API_KEY = 'x'
MAX_RETRIES: int = 3

def hello(name: str, greeting: str = "hi") -> str:
    """Greets a user."""
    return f'{greeting} {name}'

class UserService:
    """Handles user lookups."""
    timeout: float = 5.0

    async def get(self, user_id: int) -> Optional[dict]:
        """Fetch one user."""
        return None
'''

JS_SAMPLE = '''import { useState } from "react";
import axios from "axios";

export const API_URL = "https://example.com";

export async function fetchUsers(limit = 10) {
  return axios.get(API_URL);
}

const formatName = (user) => user.name.trim();

export class UserStore {
  async load(id) {
    return fetchUsers(id);
  }
}

export interface User {
  name: string;
}

export type UserId = string;
'''


def make_sample(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(PY_SAMPLE)
    return f


# ------------------------------------------------------- Python extractor --

def test_python_skeleton_keeps_signatures_drops_bodies():
    skel, symbols = core.python_skeleton(PY_SAMPLE)
    assert "def hello(name: str, greeting: str='hi') -> str" in skel
    assert "Greets a user" in skel
    assert "const API_KEY" in skel
    assert "const MAX_RETRIES" in skel
    assert "return" not in skel  # bodies are dropped


def test_python_skeleton_classes_and_methods():
    skel, symbols = core.python_skeleton(PY_SAMPLE)
    assert "class UserService" in skel
    assert "Handles user lookups" in skel
    assert "timeout: float = 5.0" in skel
    assert "async def get(self, user_id: int) -> Optional[dict]" in skel
    names = {s["name"] for s in symbols}
    assert {"hello", "UserService", "UserService.get", "API_KEY"} <= names
    by_name = {s["name"]: s for s in symbols}
    assert by_name["hello"]["line"] == 7


def test_python_syntax_error_is_safe(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n")
    skel, symbols = core.file_skeleton(f)
    assert skel == "[parse error]"
    assert symbols == []


# -------------------------------------------------------- JS/TS extractor --

def test_js_skeleton_basics():
    skel, symbols = core.js_skeleton(JS_SAMPLE)
    assert "imports: axios, react" in skel
    assert "export async function fetchUsers(limit = 10)" in skel
    assert "const formatName" in skel
    assert "export class UserStore" in skel
    assert "export interface User" in skel
    assert "export type UserId" in skel
    kinds = {s["name"]: s["kind"] for s in symbols}
    assert kinds["fetchUsers"] == "function"
    assert kinds["UserStore"] == "class"
    assert kinds["API_URL"] == "const"
    assert kinds["load"] == "method"


def test_js_methods_skip_keywords():
    src = "class A {\n  if (x) {\n  doWork(a) {\n    return a;\n  }\n}\n"
    _, symbols = core.js_skeleton(src)
    names = {s["name"] for s in symbols}
    assert "doWork" in names
    assert "if" not in names


# ------------------------------------------------------------------ cache --

def test_refresh_cache_hit_and_invalidation(tmp_path):
    f = make_sample(tmp_path)
    first = core.refresh(tmp_path)
    second = core.refresh(tmp_path)
    assert first == second
    assert "sample.py" in second
    # Modify the file -> skeleton must update
    time.sleep(0.01)
    f.write_text(PY_SAMPLE + "\ndef extra():\n    pass\n")
    third = core.refresh(tmp_path)
    assert "def extra()" in third["sample.py"]["skeleton"]


def test_refresh_skips_vendored_dirs(tmp_path):
    make_sample(tmp_path)
    vendored = tmp_path / "node_modules" / "lib"
    vendored.mkdir(parents=True)
    (vendored / "junk.py").write_text("def junk():\n    pass\n")
    data = core.refresh(tmp_path)
    assert "sample.py" in data
    assert not any("node_modules" in k for k in data)


def test_refresh_drops_deleted_files(tmp_path):
    f = make_sample(tmp_path)
    assert "sample.py" in core.refresh(tmp_path)
    f.unlink()
    assert "sample.py" not in core.refresh(tmp_path)


# ----------------------------------------------------------------- lookup --

def test_find_symbol_returns_file_and_line(tmp_path):
    make_sample(tmp_path)
    result = core.find_symbol(tmp_path, "hello")
    assert result.startswith("sample.py:7")
    assert "def hello" in result


def test_find_symbol_not_found(tmp_path):
    make_sample(tmp_path)
    assert "not found" in core.find_symbol(tmp_path, "does_not_exist")


def test_get_file_skeleton_suggests_similar(tmp_path):
    make_sample(tmp_path)
    out = core.get_file_skeleton(tmp_path, "src/sample.py")
    assert "Similar files: sample.py" in out


# -------------------------------------------------------------------- CLI --

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
    assert claude_md.count("projmap rules (auto-generated)") == 1
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "projmap" in cfg["mcpServers"]


def test_cli_map_and_find(tmp_path):
    make_sample(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")}
    r = subprocess.run([sys.executable, "-m", "projmap.cli", "map"],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "PROJECT MAP" in r.stdout and "def hello" in r.stdout
    r = subprocess.run([sys.executable, "-m", "projmap.cli", "find", "UserService"],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert "sample.py:" in r.stdout


def test_cli_uninstall_cleans_up(tmp_path):
    make_sample(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")}
    subprocess.run([sys.executable, "-m", "projmap.cli", "init"],
                   cwd=tmp_path, env=env, capture_output=True)
    r = subprocess.run([sys.executable, "-m", "projmap.cli", "uninstall"],
                       cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "projmap" not in json.loads((tmp_path / ".mcp.json").read_text()).get("mcpServers", {})
    assert "projmap rules" not in (tmp_path / "CLAUDE.md").read_text()
