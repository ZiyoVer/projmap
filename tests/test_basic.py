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


# --------------------------------------------------------- Go/Rust/Java ---

GO_SAMPLE = '''package main

import (
\t"fmt"
\t"net/http"
)

const MaxRetries = 3

type Server struct {
\tAddr string
}

func NewServer(addr string) *Server {
\treturn &Server{Addr: addr}
}

func (s *Server) Start() error {
\treturn http.ListenAndServe(s.Addr, nil)
}
'''

RUST_SAMPLE = '''use std::collections::HashMap;

pub const MAX_RETRIES: u32 = 3;

pub struct Server {
    addr: String,
}

impl Server {
    pub fn new(addr: String) -> Self {
        Server { addr }
    }
}

pub trait Handler {
    fn handle(&self) -> bool;
}

pub async fn run_server(s: Server) -> Result<(), String> {
    Ok(())
}
'''

JAVA_SAMPLE = '''package com.example;

import java.util.List;

public class UserService {
    public static final int MAX_RETRIES = 3;

    public List<String> findUsers(String query) throws Exception {
        return List.of(query);
    }

    private static boolean isValid(String name) {
        if (name == null) {
            return false;
        }
        return true;
    }
}
'''


def test_go_skeleton(tmp_path):
    f = tmp_path / "server.go"
    f.write_text(GO_SAMPLE)
    skel, symbols = core.file_skeleton(f)
    assert "imports: fmt, net/http" in skel
    assert "type Server struct" in skel
    assert "func NewServer(addr string) *Server" in skel
    kinds = {s["name"]: s["kind"] for s in symbols}
    assert kinds["NewServer"] == "function"
    assert kinds["Start"] == "method"
    assert kinds["Server"] == "type"
    assert kinds["MaxRetries"] == "const"
    assert "return" not in skel


def test_rust_skeleton(tmp_path):
    f = tmp_path / "server.rs"
    f.write_text(RUST_SAMPLE)
    skel, symbols = core.file_skeleton(f)
    assert "imports: std::collections::HashMap" in skel
    assert "pub struct Server" in skel
    assert "pub async fn run_server(s: Server) -> Result<(), String>" in skel
    kinds = {s["name"]: s["kind"] for s in symbols}
    assert kinds["run_server"] == "function"
    assert kinds["new"] == "method"
    assert kinds["Handler"] == "trait"
    assert kinds["MAX_RETRIES"] == "const"


def test_java_skeleton(tmp_path):
    f = tmp_path / "UserService.java"
    f.write_text(JAVA_SAMPLE)
    skel, symbols = core.file_skeleton(f)
    assert "imports: java.util.List" in skel
    assert "public class UserService" in skel
    assert "public List<String> findUsers(String query) throws Exception" in skel
    kinds = {s["name"]: s["kind"] for s in symbols}
    assert kinds["UserService"] == "class"
    assert kinds["findUsers"] == "method"
    assert kinds["MAX_RETRIES"] == "const"
    assert "if" not in kinds  # control flow is not a method


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


def test_gitignore_is_respected(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    make_sample(tmp_path)
    (tmp_path / ".gitignore").write_text("generated.py\n")
    (tmp_path / "generated.py").write_text("def gen():\n    pass\n")
    data = core.refresh(tmp_path)
    assert "sample.py" in data
    assert "generated.py" not in data


def test_build_map_scoped_to_subdir(tmp_path):
    make_sample(tmp_path)
    sub = tmp_path / "api"
    sub.mkdir()
    (sub / "routes.py").write_text("def route():\n    pass\n")
    m = core.build_map(tmp_path, "api")
    assert "api/routes.py" in m
    assert "sample.py" not in m
    assert "No source files under" in core.build_map(tmp_path, "nope")


def test_build_map_header_has_token_estimate(tmp_path):
    make_sample(tmp_path)
    m = core.build_map(tmp_path)
    assert "tokens vs ~" in m.splitlines()[0]


def test_build_map_is_size_capped(tmp_path, monkeypatch):
    for i in range(30):
        (tmp_path / f"mod_{i:02d}.py").write_text(PY_SAMPLE)
    monkeypatch.setattr(core, "MAX_MAP_CHARS", 2000)
    m = core.build_map(tmp_path)
    assert len(m) < 3000
    assert "map truncated" in m
    assert "projmap_file_skeleton" in m


def test_refresh_respects_file_cap(tmp_path, monkeypatch):
    for i in range(10):
        (tmp_path / f"mod_{i:02d}.py").write_text("X = 1\n")
    monkeypatch.setattr(core, "MAX_FILES", 4)
    assert len(core.refresh(tmp_path)) == 4


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


# ----------------------------------------------------------------- memory --

def test_notes_roundtrip(tmp_path):
    assert "no project notes yet" in core.read_notes(tmp_path)
    out = core.add_note(tmp_path, "  Auth uses   JWT tokens  ")
    assert out == "Saved: Auth uses JWT tokens"
    notes = core.read_notes(tmp_path)
    assert "Auth uses JWT tokens" in notes
    assert notes.startswith("# Project notes")
    core.add_note(tmp_path, "DB is Postgres")
    assert core.read_notes(tmp_path).count("- [") == 2


def test_changed_files_map(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    f = make_sample(tmp_path)
    out = core.changed_files_map(tmp_path)
    assert "sample.py" in out and "def hello" in out
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], cwd=tmp_path, capture_output=True)
    assert "No modified or untracked" in core.changed_files_map(tmp_path)
    f.write_text(PY_SAMPLE + "\ndef extra():\n    pass\n")
    assert "def extra()" in core.changed_files_map(tmp_path)


def test_changed_files_without_git(tmp_path):
    make_sample(tmp_path)
    assert "Not a git repository" in core.changed_files_map(tmp_path)


# -------------------------------------------------------------------- CLI --

def test_repo_root_never_climbs_to_home(tmp_path, monkeypatch):
    """A project without .git must not resolve to a home dir that has .git."""
    from projmap import cli
    fake_home = tmp_path / "home"
    project = fake_home / "myproject"
    project.mkdir(parents=True)
    (fake_home / ".git").mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.chdir(project)
    assert cli._find_repo_root() == project
    # but running directly inside home still works
    monkeypatch.chdir(fake_home)
    assert cli._find_repo_root() == fake_home


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


def test_cli_init_concise(tmp_path):
    make_sample(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")}
    for _ in range(2):
        r = subprocess.run(
            [sys.executable, "-m", "projmap.cli", "init", "--concise"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
    claude_md = (tmp_path / "CLAUDE.md").read_text()
    assert claude_md.count("projmap concise output") == 1
    assert claude_md.count("projmap rules (auto-generated)") == 1
    # uninstall removes the concise section too
    subprocess.run([sys.executable, "-m", "projmap.cli", "uninstall"],
                   cwd=tmp_path, env=env, capture_output=True)
    assert "concise" not in (tmp_path / "CLAUDE.md").read_text()


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
