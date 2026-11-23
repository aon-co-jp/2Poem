"""中身が静的な HTML テンプレートだけを、そのまま返すハンドラへ移す(ast だけ。テンプレートは実行しない)。

対象: 関数本体が単一の `return render_template("x.html")` / `return render(request, "x.html")`(文脈の引数なし)で、
`x.html` が見つかり、`{{` `{%` `{#` を含まないとき。変数・タグを含むテンプレートは移さない(誤訳より未移植)。
"""
import ast
import os
import textwrap


def match_static(source, source_root):
    """(出力先のファイル名, 本文) を返す。合わなければ None。"""
    if not source or not source_root:
        return None
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return None
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if fn is None:
        return None
    body = [b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
        return None
    c = body[0].value
    fname = ast.unparse(c.func).split(".")[-1]
    if c.keywords:
        return None
    if fname == "render_template" and len(c.args) == 1:
        arg = c.args[0]
    elif fname == "render" and len(c.args) == 2:
        arg = c.args[1]
    else:
        return None
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    rel = arg.value.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/") or not rel.lower().endswith((".html", ".htm")):
        return None
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "target", "venv", ".venv", "__pycache__")]
        cand = os.path.join(root, *rel.split("/"))
        if os.path.isfile(cand):
            try:
                text = open(cand, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                return None
            if "{{" in text or "{%" in text or "{#" in text:
                return None
            return rel.replace("/", "_"), text
    return None
