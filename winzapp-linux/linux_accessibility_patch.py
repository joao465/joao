from __future__ import annotations

import ast
import sys
from pathlib import Path


def _is_set_accessible_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SetAccessible"
    )


def _win32_test() -> ast.expr:
    # Use __import__('sys') so we do not need to modify imports in every UI file.
    return ast.Compare(
        left=ast.Attribute(
            value=ast.Call(
                func=ast.Name(id="__import__", ctx=ast.Load()),
                args=[ast.Constant(value="sys")],
                keywords=[],
            ),
            attr="platform",
            ctx=ast.Load(),
        ),
        ops=[ast.Eq()],
        comparators=[ast.Constant(value="win32")],
    )


class AccessibilityTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.patched = 0

    def visit_Expr(self, node: ast.Expr):
        node = self.generic_visit(node)
        if isinstance(node, ast.Expr) and _is_set_accessible_call(node.value):
            self.patched += 1
            wrapped = ast.If(test=_win32_test(), body=[node], orelse=[])
            return ast.copy_location(wrapped, node)
        return node


def patch_file(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if ".SetAccessible(" not in source:
        return 0

    tree = ast.parse(source, filename=str(path))
    transformer = AccessibilityTransformer()
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)

    if transformer.patched:
        path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
    return transformer.patched


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: linux_accessibility_patch.py <WinZapp source root>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    client = root / "client"
    if not client.is_dir():
        print(f"client directory not found: {client}", file=sys.stderr)
        return 2

    total = 0
    touched: list[tuple[Path, int]] = []
    for path in sorted(client.rglob("*.py")):
        count = patch_file(path)
        if count:
            total += count
            touched.append((path, count))

    print(f"Linux accessibility patch: guarded {total} wx SetAccessible call(s)")
    for path, count in touched:
        print(f"  {path.relative_to(root)}: {count}")

    if total == 0:
        print("ERROR: no SetAccessible calls were found; upstream source may have changed", file=sys.stderr)
        return 1

    # The crash observed on Linux came from this exact constructor path.
    conversations = client / "ui" / "conversations.py"
    if conversations.exists():
        compile(conversations.read_text(encoding="utf-8"), str(conversations), "exec")

    # Compile all touched modules now so the CI fails before PyInstaller if the
    # transformation ever produces invalid Python.
    for path, _ in touched:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
