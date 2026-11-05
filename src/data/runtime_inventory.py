"""数据运行入口的只读 Python 导入闭包；供打包和边界门禁共同使用。"""

import ast
from pathlib import Path


def runtime_inventory(repository, entry_files):
    repository = Path(repository).resolve()
    pending = [repository / value for value in entry_files]
    visited = set()
    imports = []
    dynamic = []
    lazy_exports = {}

    def lazy_target(parts, name):
        key = tuple(parts)
        if key not in lazy_exports:
            values = {}
            path = repository.joinpath(*parts, "__init__.py")
            if path.is_file():
                for assignment in ast.walk(ast.parse(path.read_text())):
                    if (isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Dict)
                            and any(isinstance(target, ast.Name) and target.id == "_lazy_map"
                                    for target in assignment.targets)):
                        try:
                            values.update(ast.literal_eval(assignment.value))
                        except (TypeError, ValueError):
                            raise ValueError("包的延迟导出缺少可核对的字面量目标") from None
            lazy_exports[key] = values
        return lazy_exports[key].get(name)

    def enqueue(parts):
        for length in range(1, len(parts) + 1):
            base = repository.joinpath(*parts[:length])
            package = base / "__init__.py"
            if package.is_file():
                pending.append(package)
        base = repository.joinpath(*parts)
        module = base.with_suffix(".py")
        if module.is_file():
            pending.append(module)

    while pending:
        path = pending.pop()
        if path in visited:
            continue
        if not path.is_file() or not path.resolve().is_relative_to(repository):
            raise ValueError("数据运行闭包路径缺失或越界")
        visited.add(path)
        relative = str(path.relative_to(repository))
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def owner(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return "<module>"

        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = list(path.parent.relative_to(repository).parts)
                    parts = parts[:len(parts) - node.level + 1]
                    parts.extend(node.module.split(".") if node.module else [])
                else:
                    parts = node.module.split(".") if node.module else []
                if parts:
                    modules.append(parts)
                for alias in node.names:
                    enqueue(parts + alias.name.split("."))
                    target = lazy_target(parts, alias.name)
                    if isinstance(target, str):
                        modules.append(target.split("."))
            elif isinstance(node, ast.Call):
                function = ast.unparse(node.func)
                if function == "__import__" or function == "import_module" or function.endswith(".import_module"):
                    argument = node.args[0] if node.args else None
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        modules.append(argument.value.split("."))
                    else:
                        dynamic.append({"path": relative, "function": owner(node), "line": node.lineno,
                                        "expression": ast.unparse(argument) if argument is not None else ""})
            for parts in modules:
                enqueue(parts)
                imports.append({"path": relative, "module": ".".join(parts), "line": node.lineno})
    return {"files": sorted(str(path.relative_to(repository)) for path in visited),
            "imports": imports, "dynamicImports": dynamic}


def data_runtime_inventory(repository):
    repository = Path(repository)
    entries = ["server.py"]
    # Provider 工厂可能延迟导入，SDK 适配器与精确来源定义均纳入静态检查。
    entries.extend(str(path.relative_to(repository)) for path in (repository / "data_provider").rglob("*.py"))
    return runtime_inventory(repository, entries)
