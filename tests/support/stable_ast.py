"""A syntax-tree rendering that is identical across the supported Python versions, for hashed baselines."""

import ast
from typing import cast


def stable_dump(node: ast.AST) -> str:
    """Render node by class and populated fields, so a version-added empty field does not change the text."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        # Python 3.13 strips docstring indentation at parse time, so only the words are comparable.
        return f"Expr(docstring={' '.join(node.value.value.split())!r})"
    parts: list[str] = []
    for name in node._fields:
        value: object = getattr(node, name, None)
        if isinstance(node, ast.JoinedStr) and name == "values":
            # Python 3.12 keeps an empty literal piece in a format spec that later versions drop.
            value = [
                part for part in cast(list[object], value) if not (isinstance(part, ast.Constant) and part.value == "")
            ]
        if value is None or value == []:
            continue
        parts.append(f"{name}={_render(value)}")
    return f"{type(node).__name__}({', '.join(parts)})"


def _render(value: object) -> str:
    if isinstance(value, ast.AST):
        return stable_dump(value)
    if isinstance(value, list):
        return "[" + ", ".join(_render(item) for item in cast(list[object], value)) + "]"
    return repr(value)


def unparse(node: ast.AST) -> str:
    """Source text of node, with double quotes swapped for single where that parses back to the same tree.

    Python 3.12 quotes an f-string with single quotes and 3.14 with double, so the swap makes both read alike.
    """
    text = ast.unparse(node)
    swapped = text.replace('"', "'")
    if swapped == text:
        return text
    try:
        same = stable_dump(ast.parse(swapped, mode="eval")) == stable_dump(ast.parse(text, mode="eval"))
    except SyntaxError:
        return text
    return swapped if same else text
