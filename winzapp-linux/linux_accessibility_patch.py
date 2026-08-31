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


def _is_wx_accessible_base(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "wx"
        and node.attr == "Accessible"
    )


def _is_super_init_call(node: ast.AST) -> bool:
    """Return True for a plain ``super().__init__(...)`` expression call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "__init__"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id == "super"
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
        self.guarded_setaccessible = 0
        self.guarded_accessible_init = 0
        self._inside_wx_accessible = 0

    @property
    def patched(self) -> int:
        return self.guarded_setaccessible + self.guarded_accessible_init

    def visit_ClassDef(self, node: ast.ClassDef):
        is_wx_accessible = any(_is_wx_accessible_base(base) for base in node.bases)
        if is_wx_accessible:
            self._inside_wx_accessible += 1
        try:
            return self.generic_visit(node)
        finally:
            if is_wx_accessible:
                self._inside_wx_accessible -= 1

    def visit_Expr(self, node: ast.Expr):
        node = self.generic_visit(node)
        if not isinstance(node, ast.Expr):
            return node

        # wxGTK does not implement wx.Window.SetAccessible/wx.Accessible in the
        # same MSAA-oriented way as wxMSW. Keep GTK's native AT-SPI exposure.
        if _is_set_accessible_call(node.value):
            self.guarded_setaccessible += 1
            wrapped = ast.If(test=_win32_test(), body=[node], orelse=[])
            return ast.copy_location(wrapped, node)

        # Some WinZapp code constructs an Accessible object on one line and
        # calls SetAccessible on the next. Guarding only SetAccessible is too
        # late: wxGTK raises NotImplementedError in wx.Accessible.__init__().
        # Skip that base constructor on Linux so creating the helper object is
        # harmless; the SetAccessible call itself is already Windows-only.
        if self._inside_wx_accessible and _is_super_init_call(node.value):
            self.guarded_accessible_init += 1
            wrapped = ast.If(test=_win32_test(), body=[node], orelse=[])
            return ast.copy_location(wrapped, node)

        return node


def patch_file(path: Path) -> tuple[int, int]:
    source = path.read_text(encoding="utf-8")
    if ".SetAccessible(" not in source and "wx.Accessible" not in source:
        return (0, 0)

    tree = ast.parse(source, filename=str(path))
    transformer = AccessibilityTransformer()
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)

    if transformer.patched:
        path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
    return (transformer.guarded_setaccessible, transformer.guarded_accessible_init)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: linux_accessibility_patch.py <WinZapp source root>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    client = root / "client"
    if not client.is_dir():
        print(f"client directory not found: {client}", file=sys.stderr)
        return 2

    total_set = 0
    total_init = 0
    touched: list[tuple[Path, int, int]] = []
    for path in sorted(client.rglob("*.py")):
        set_count, init_count = patch_file(path)
        if set_count or init_count:
            total_set += set_count
            total_init += init_count
            touched.append((path, set_count, init_count))

    print(
        "Linux accessibility patch: guarded "
        f"{total_set} wx SetAccessible call(s) and "
        f"{total_init} wx.Accessible constructor call(s)"
    )
    for path, set_count, init_count in touched:
        print(
            f"  {path.relative_to(root)}: "
            f"SetAccessible={set_count}, Accessible.__init__={init_count}"
        )

    if total_set == 0:
        print("ERROR: no SetAccessible calls were found; upstream source may have changed", file=sys.stderr)
        return 1
    if total_init == 0:
        print("ERROR: no wx.Accessible constructors were guarded; Linux crash protection is incomplete", file=sys.stderr)
        return 1

    # The original and post-login crashes both came from conversations.py /
    # ui.accessible.py paths. Compile them explicitly before PyInstaller.
    for check in (client / "ui" / "conversations.py", client / "ui" / "accessible.py"):
        if check.exists():
            compile(check.read_text(encoding="utf-8"), str(check), "exec")

    # Compile all touched modules now so CI fails early if a transformation ever
    # produces invalid Python.
    for path, _, _ in touched:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
