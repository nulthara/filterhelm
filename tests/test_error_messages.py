"""Guard PiholeAPIError messages against dynamic upstream content."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pihole_error_names(tree_map: dict[str, ast.Module]) -> set[str]:
    names = {"PiholeAPIError"}
    while True:
        derived = {
            node.name
            for tree in tree_map.values()
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id in names for base in node.bases)
        }
        if derived <= names:
            return names
        names |= derived


def _violations(source: str, filename: str, names: set[str]) -> list[str]:
    tree = ast.parse(source, filename=filename)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def is_string(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def is_status(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "resp"
            and node.attr == "status_code"
        )

    def safe_message(node: ast.AST, call: ast.Call) -> bool:
        if is_string(node):
            return True
        if isinstance(node, ast.JoinedStr):
            return all(
                is_string(part)
                or (
                    isinstance(part, ast.FormattedValue)
                    and (is_status(part.value) or isinstance(part.value, ast.Name) and part.value.id == "path")
                    and (part.format_spec is None or safe_message(part.format_spec, call))
                )
                for part in node.values
            )
        if not isinstance(node, ast.Name):
            return False
        enclosing = parents.get(call)
        while enclosing is not None and not isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = parents.get(enclosing)
        if enclosing is None:
            return False
        # Nested functions and classes do not bind names in this function.
        scope_nodes: list[ast.AST] = []
        pending = list(enclosing.body)
        while pending:
            statement = pending.pop()
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            scope_nodes.append(statement)
            pending.extend(ast.iter_child_nodes(statement))
        assignments = [
            assignment
            for assignment in scope_nodes
            if isinstance(assignment, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == node.id
                for target in assignment.targets
            )
        ]
        return bool(assignments) and all(is_string(assignment.value) for assignment in assignments)

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in names:
            continue
        safe = len(node.args) == 1 and safe_message(node.args[0], node)
        safe = safe and all(
            keyword.arg == "status_code"
            and (
                isinstance(keyword.value, ast.Constant) and type(keyword.value.value) is int
                or is_status(keyword.value)
            )
            for keyword in node.keywords
        )
        if not safe:
            violations.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
    return violations


def test_pihole_error_messages_are_static() -> None:
    sources = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app").rglob("*.py"))
        if not path.is_relative_to(ROOT / "app" / "static")
    }
    names = _pihole_error_names({filename: ast.parse(source) for filename, source in sources.items()})
    violations = [
        violation
        for filename, source in sources.items()
        for violation in _violations(source, filename, names)
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    'PiholeAPIError("fixed")',
    'PiholeAPIError(f"HTTP {resp.status_code}")',
    'PiholeAPIError(f"POST {path}")',
    'def run():\n    error = "x"\n    PiholeAPIError(error)',
])
def test_scanner_accepts_safe_shapes(source: str) -> None:
    assert _violations(source, "snippet.py", {"PiholeAPIError"}) == []


@pytest.mark.parametrize("message", [
    "str(exc)", 'f"{exc}"', 'f"{resp.text}"', 'resp.json()["detail"]',
    '"a" + x', '"%s" % x', '"{}".format(x)', "", 'resp.content',
])
def test_scanner_rejects_dynamic_messages(message: str) -> None:
    source = f"PiholeAPIError({message})"
    assert _violations(source, "snippet.py", {"PiholeAPIError"}) == [
        f"snippet.py:1: {ast.unparse(ast.parse(source).body[0].value)}"
    ]


def test_scanner_rejects_reassigned_error_variable() -> None:
    source = 'def run():\n    error = "x"\n    error = str(exc)\n    PiholeAPIError(error)'
    assert len(_violations(source, "snippet.py", {"PiholeAPIError"})) == 1


@pytest.mark.parametrize("source", [
    'def run():\n    PiholeAPIError(error)',
    'error = "x"\nPiholeAPIError(error)',
    'def run():\n    def nested():\n        error = "x"\n    PiholeAPIError(error)',
])
def test_scanner_rejects_unbound_name(source: str) -> None:
    assert len(_violations(source, "snippet.py", {"PiholeAPIError"})) == 1


def test_scanner_follows_subclasses() -> None:
    sources = {
        "child.py": "class Child(Foo): pass\nChild(str(exc))",
        "base.py": "class Foo(PiholeAPIError): pass\nFoo(str(exc))",
        "action.py": "class PiholeActionUnconfirmedError(PiholeAPIError): pass\nPiholeActionUnconfirmedError(str(exc))",
    }
    names = _pihole_error_names({filename: ast.parse(source) for filename, source in sources.items()})
    assert names == {"PiholeAPIError", "Foo", "Child", "PiholeActionUnconfirmedError"}
    for filename, source in sources.items():
        assert len(_violations(source, filename, names)) == 1


@pytest.mark.parametrize(("keyword", "count"), [
    ("status_code=int(x)", 1), ("status_code=403", 0),
    ("status_code=resp.status_code", 0), ("detail=resp.text", 1),
    ("**kwargs", 1),
])
def test_scanner_rejects_dynamic_status_code_keyword(keyword: str, count: int) -> None:
    assert len(_violations(f'PiholeAPIError("fixed", {keyword})', "snippet.py", {"PiholeAPIError"})) == count
