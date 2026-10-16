"""生产路径只有一个 Agent 及必要组件，避免重引入旧工作流。"""

import ast
import tomllib
from pathlib import Path


def test_no_custom_graph_or_removed_business_modules():
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "maidanlao"
    for name in ["repositories", "services", "security", "schemas", "tools", "memory"]:
        assert not list((source / name).glob("*.py"))
    for name in ["graph.py", "runner.py", "runtime.py", "state.py", "edges.py"]:
        assert not (source / "agent" / name).exists()
    assert not (source / "persistence" / "checkpoints.py").exists()
    assert not (source / "notifications" / "parser.py").exists()
    assert not list((root / "deploy").glob("*reminder*"))
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        assert not any(module and module.startswith("langgraph") for module in imports), path


def test_dependency_manifest_and_lock_keep_langgraph_transitive_only():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(item.startswith("langchain>=") for item in project["dependencies"])
    assert not any(item.startswith("langgraph") for item in project["dependencies"])
    assert "graph" not in project["optional-dependencies"]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = {item["name"] for item in lock["package"]}
    assert {"langchain", "langgraph", "sqlalchemy"} <= packages
    assert "langgraph-checkpoint-sqlite" not in packages
