"""`.env.example` must name every environment variable the application reads.

Platform variables injected by Azure or GitHub Actions (PORT, WEBSITE_*,
GITHUB_*) are allowlisted. A variable read through os.getenv, os.environ.get,
or os.environ[...] has to appear in `.env.example`, including when the name is
passed through a local helper or a constant tuple.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EXAMPLE_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=")
_SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "tests",
    "venv",
}
_PLATFORM_EXACT = {"PORT"}
_PLATFORM_PREFIXES = ("GITHUB_", "WEBSITE_")


def _is_platform(name: str) -> bool:
    return name in _PLATFORM_EXACT or name.startswith(_PLATFORM_PREFIXES)


def documented_names(text: str | None = None) -> set[str]:
    """Return names assigned in `.env.example`, including commented defaults."""
    if text is None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
    names = set()
    for line in text.splitlines():
        match = _EXAMPLE_ASSIGNMENT.match(line)
        if match:
            names.add(match.group(1))
    return names


def _is_env_name(value: object) -> bool:
    return isinstance(value, str) and _ENV_NAME.fullmatch(value) is not None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_sequence(
    node: ast.AST, constants: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    names: list[str] = []
    for elt in node.elts:
        if not isinstance(elt, ast.Constant) or not _is_env_name(elt.value):
            return None
        names.append(elt.value)
    return tuple(names)


def _expr_is_environ(node: ast.AST, aliases: set[str]) -> bool:
    if _is_os_environ(node):
        return True
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    if isinstance(node, ast.IfExp):
        return _expr_is_environ(node.body, aliases) or _expr_is_environ(
            node.orelse, aliases
        )
    return False


@dataclass
class _Ctx:
    constants: dict[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: set[str] = field(default_factory=set)
    loop_vars: dict[str, ast.AST] = field(default_factory=dict)
    forwarded: set[str] = field(default_factory=set)

    def spawn(self, **updates: object) -> _Ctx:
        payload = {
            "constants": dict(self.constants),
            "aliases": set(self.aliases),
            "loop_vars": dict(self.loop_vars),
            "forwarded": set(self.forwarded),
        }
        payload.update(updates)
        return _Ctx(**payload)


class _Scanner:
    def __init__(self, path: str, direct_getenv: set[str], imported_environ: set[str]):
        self.path = path
        self.direct_getenv = direct_getenv
        self.imported_environ = imported_environ
        self.forwarders: dict[str, set[int]] = {}
        self.reads: dict[str, list[str]] = {}
        self.unresolved: list[str] = []

    def _add(self, name: str, lineno: int) -> None:
        self.reads.setdefault(name, []).append(f"{self.path}:{lineno}")

    def _unresolved(self, node: ast.AST, detail: str) -> None:
        line = getattr(node, "lineno", "?")
        self.unresolved.append(f"{self.path}:{line}: {detail}")

    def _is_direct_getenv(self, node: ast.Call, aliases: set[str]) -> bool:
        func = node.func
        if isinstance(func, ast.Name) and func.id in self.direct_getenv:
            return True
        if not isinstance(func, ast.Attribute):
            return False
        if (
            func.attr == "getenv"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            return True
        if func.attr != "get":
            return False
        if _is_os_environ(func.value):
            return True
        if isinstance(func.value, ast.Name) and (
            func.value.id in aliases or func.value.id in self.imported_environ
        ):
            return True
        return False

    def _is_environ_subscript(self, node: ast.Subscript, aliases: set[str]) -> bool:
        value = node.value
        if _is_os_environ(value):
            return True
        return isinstance(value, ast.Name) and (
            value.id in aliases or value.id in self.imported_environ
        )

    def _record_key(self, key: ast.AST, ctx: _Ctx) -> None:
        if isinstance(key, ast.Constant):
            if _is_env_name(key.value):
                self._add(key.value, key.lineno)
            else:
                self._unresolved(key, "environment read is not an uppercase name")
            return
        if isinstance(key, ast.Name):
            if key.id in ctx.forwarded:
                return
            if key.id in ctx.loop_vars:
                names = _string_sequence(ctx.loop_vars[key.id], ctx.constants)
                if names is None:
                    self._unresolved(key, f"could not resolve env names from {key.id}")
                else:
                    for name in names:
                        self._add(name, key.lineno)
                return
            self._unresolved(key, f"could not resolve env name {key.id}")
            return
        self._unresolved(key, "environment name is not a literal")

    def _record_call(self, node: ast.Call, ctx: _Ctx) -> None:
        if self._is_direct_getenv(node, ctx.aliases):
            if node.args:
                self._record_key(node.args[0], ctx)
            else:
                self._unresolved(node, "environment read has no name")
            return
        name = _called_name(node)
        indexes = self.forwarders.get(name or "")
        if not indexes:
            return
        for index in indexes:
            if index < len(node.args):
                self._record_key(node.args[index], ctx)
            else:
                self._unresolved(node, f"{name} env argument is missing")

    def _bind_simple_assign(self, target: ast.AST, value: ast.AST, ctx: _Ctx) -> bool:
        if not isinstance(target, ast.Name):
            return False
        sequence = _string_sequence(value, ctx.constants)
        if sequence is not None:
            ctx.constants[target.id] = sequence
            return True
        if _expr_is_environ(value, ctx.aliases):
            ctx.aliases.add(target.id)
            return True
        return False

    def visit(self, node: ast.AST, ctx: _Ctx) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                self.visit(decorator, ctx)
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    self.visit(default, ctx)
            if node.returns is not None:
                self.visit(node.returns, ctx)
            forwarded = {
                arg.arg
                for index, arg in enumerate(node.args.args)
                if index in self.forwarders.get(node.name, set())
            }
            inner = ctx.spawn(forwarded=forwarded)
            for stmt in node.body:
                self.visit(stmt, inner)
            return

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            self.visit(node.value, ctx)
            if self._bind_simple_assign(node.targets[0], node.value, ctx):
                return
            return

        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.visit(node.value, ctx)
                self._bind_simple_assign(node.target, node.value, ctx)
            return

        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            self.visit(node.iter, ctx)
            inner = ctx.spawn(loop_vars={**ctx.loop_vars, node.target.id: node.iter})
            for stmt in node.body:
                self.visit(stmt, inner)
            for stmt in node.orelse:
                self.visit(stmt, ctx)
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            inner = self._comprehension_ctx(node.generators, ctx)
            self.visit(node.elt, inner)
            return

        if isinstance(node, ast.DictComp):
            inner = self._comprehension_ctx(node.generators, ctx)
            self.visit(node.key, inner)
            self.visit(node.value, inner)
            return

        if isinstance(node, ast.Call):
            self._record_call(node, ctx)
        elif isinstance(node, ast.Subscript) and self._is_environ_subscript(
            node, ctx.aliases
        ):
            self._record_key(node.slice, ctx)

        for child in ast.iter_child_nodes(node):
            self.visit(child, ctx)

    def _comprehension_ctx(
        self, generators: list[ast.comprehension], ctx: _Ctx
    ) -> _Ctx:
        inner = ctx
        for generator in generators:
            self.visit(generator.iter, inner)
            if isinstance(generator.target, ast.Name):
                inner = inner.spawn(
                    loop_vars={**inner.loop_vars, generator.target.id: generator.iter}
                )
            for test in generator.ifs:
                self.visit(test, inner)
        return inner


def _module_constants(tree: ast.AST) -> dict[str, tuple[str, ...]]:
    constants: dict[str, tuple[str, ...]] = {}
    for stmt in getattr(tree, "body", []):
        value = None
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            value = stmt.value
        if isinstance(target, ast.Name) and value is not None:
            sequence = _string_sequence(value, constants)
            if sequence is not None:
                constants[target.id] = sequence
    return constants


def _imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    getenv_names: set[str] = set()
    environ_names: set[str] = set()
    for stmt in getattr(tree, "body", []):
        if not isinstance(stmt, ast.ImportFrom) or stmt.module != "os":
            continue
        for alias in stmt.names:
            bound = alias.asname or alias.name
            if alias.name == "getenv":
                getenv_names.add(bound)
            elif alias.name == "environ":
                environ_names.add(bound)
    return getenv_names, environ_names


def _param_reaches_reader(
    func: ast.AST, param: str, forwarders: dict[str, set[int]], getenv_names: set[str]
) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            func_node = node.func
            direct = (
                isinstance(func_node, ast.Name) and func_node.id in getenv_names
            ) or (
                isinstance(func_node, ast.Attribute)
                and (
                    (
                        func_node.attr == "getenv"
                        and isinstance(func_node.value, ast.Name)
                        and func_node.value.id == "os"
                    )
                    or (func_node.attr == "get" and _is_os_environ(func_node.value))
                )
            )
            if (
                direct
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == param
            ):
                return True
            called = _called_name(node)
            for index in forwarders.get(called or "", set()):
                if (
                    index < len(node.args)
                    and isinstance(node.args[index], ast.Name)
                    and node.args[index].id == param
                ):
                    return True
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            if isinstance(node.slice, ast.Name) and node.slice.id == param:
                return True
    return False


def _find_forwarders(tree: ast.AST, getenv_names: set[str]) -> dict[str, set[int]]:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    forwarders: dict[str, set[int]] = {}
    changed = True
    while changed:
        changed = False
        for func in functions:
            for index, arg in enumerate(func.args.args):
                indexes = forwarders.setdefault(func.name, set())
                if index in indexes:
                    continue
                if _param_reaches_reader(func, arg.arg, forwarders, getenv_names):
                    indexes.add(index)
                    changed = True
            if func.name in forwarders and not forwarders[func.name]:
                del forwarders[func.name]
    return forwarders


def env_reads_in_source(
    source: str, path: str = "<memory>"
) -> tuple[dict[str, list[str]], list[str]]:
    tree = ast.parse(source)
    getenv_names, environ_names = _imports(tree)
    scanner = _Scanner(path, getenv_names, environ_names)
    scanner.forwarders = _find_forwarders(tree, getenv_names)
    ctx = _Ctx(constants=_module_constants(tree))
    scanner.visit(tree, ctx)
    return scanner.reads, scanner.unresolved


def python_sources() -> list[Path]:
    paths = []
    for path in ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        paths.append(path)
    return sorted(paths)


def env_reads_in_repo() -> tuple[dict[str, list[str]], list[str]]:
    reads: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for path in python_sources():
        source = path.read_text(encoding="utf-8")
        found, pending = env_reads_in_source(source, path.relative_to(ROOT).as_posix())
        for name, locations in found.items():
            reads.setdefault(name, []).extend(locations)
        unresolved.extend(pending)
    return reads, unresolved


def test_scanner_follows_helpers_subscripts_and_name_tuples():
    source = """
import os
from os import getenv as read_env

NAMES = ("PLATFORM_A", "PLATFORM_B")

def _env_int(name, default):
    return int(os.getenv(name, default))

def nested(label):
    return _env_int(label, 1)

LIMIT = _env_int("WIDGET_LIMIT", 1)
TOKEN = os.environ.get("WIDGET_TOKEN")
MODE = os.environ["WIDGET_MODE"]
ALIAS = read_env("WIDGET_ALIAS")

def check():
    env = os.environ if True else os.environ
    return [env.get(name) for name in NAMES]

def forwarded(label):
    return nested(label)

FORWARDED = forwarded("WIDGET_FORWARDED")
"""
    reads, unresolved = env_reads_in_source(source)
    assert unresolved == []
    assert set(reads) == {
        "PLATFORM_A",
        "PLATFORM_B",
        "WIDGET_ALIAS",
        "WIDGET_FORWARDED",
        "WIDGET_LIMIT",
        "WIDGET_MODE",
        "WIDGET_TOKEN",
    }


def test_env_example_matches_variables_the_code_reads():
    reads, unresolved = env_reads_in_repo()
    documented = documented_names()
    missing = sorted(
        name for name in reads if name not in documented and not _is_platform(name)
    )
    stale = sorted(documented - set(reads))
    assert unresolved == [], "Unresolved environment reads:\n" + "\n".join(unresolved)
    missing_detail = []
    for name in missing:
        where = ", ".join(reads[name])
        missing_detail.append(f"{name} ({where})")
    assert (
        missing == []
    ), "Code reads environment variables missing from .env.example:\n" + "\n".join(
        missing_detail
    )
    assert stale == [], (
        ".env.example documents environment variables the code does not read:\n"
        + "\n".join(stale)
    )


def test_platform_variables_are_allowlisted_and_not_copy_paste_settings():
    reads, _unresolved = env_reads_in_repo()
    documented = documented_names()
    for name in ("PORT", "WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID", "GITHUB_SHA"):
        assert name in reads
        assert name not in documented
        assert _is_platform(name)
    assert "SESSION_SECRET_KEY" in reads
    assert not _is_platform("SESSION_SECRET_KEY")
    assert "SESSION_SECRET_KEY" in documented


def test_deploy_docs_name_the_startup_command_and_durable_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    azure = (ROOT / "AZURE_DEPLOYMENT.md").read_text(encoding="utf-8")
    command = "gunicorn --config gunicorn.conf.py app:app"
    for text in (readme, azure):
        assert command in text
        assert "gunicorn --bind" not in text
        assert "gunicorn app:app" not in text
        assert "/healthz" in text
        assert "/readyz" in text
        assert "/home/data/chroma" in text
        assert "/home/data/evaluation/reviews.sqlite3" in text
        assert "/tmp/chroma" in text
        assert "VECTOR_STORE_PATH" in text
    assert "HTTPS Only is enabled" in readme
    assert "HTTPS Only" in azure
    assert "docs/ops-log.md" in azure
    assert "docs/ops-log.md" in readme
