"""ORM の「全件取得」と「件数」だけを、sqlx(PostgreSQL、純 Rust)の問い合わせへ移す(ast だけ。実行はしない)。

対象(関数本体が、docstring を除いて単一の return のとき。ラッパーは jsonify / JSONResponse / JsonResponse / list):
  - Django:     `M.objects.all()` / `list(M.objects.values())` / `M.objects.count()` / `M.objects.all().count()`
  - SQLAlchemy: `session.query(M).all()` / `db.session.query(M).count()` / `M.query.all()` / `M.query.count()`
  - SQLModel:   `session.exec(select(M)).all()`
それ以外(filter・get・order_by・作成・更新・削除・結合)は移さず、ハンドラは TODO のまま(誤訳より未移植)。
"""
import ast
import textwrap

WRAPPERS = ("jsonify", "JSONResponse", "JsonResponse", "list")


def _unwrap(v):
    while isinstance(v, ast.Call) and len(v.args) == 1 and all(k.arg == "safe" for k in v.keywords) and ast.unparse(v.func).split(".")[-1] in WRAPPERS:
        v = v.args[0]
    return v


def _chain(v):
    """`a.b(x).c()` → [("b", [x]), ("c", [])](左から)と、いちばん内側の式。"""
    calls = []
    while isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute):
        calls.append((v.func.attr, v.args, v.keywords))
        v = v.func.value
    calls.reverse()
    return v, calls


def _name(n):
    return n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else "")


def _lit(v):
    c = v.value if isinstance(v, ast.Constant) else None
    if isinstance(c, bool):
        return "TRUE" if c else "FALSE"
    if isinstance(c, int):
        return str(c)
    if isinstance(c, str) and all(32 <= ord(ch) < 127 and ch not in ('"', chr(92)) for ch in c):
        return "'" + c.replace("'", "''") + "'"
    return None


def _where(keywords, root, by_name):
    if not (isinstance(root, ast.Attribute) and root.attr == "objects" and isinstance(root.value, ast.Name)):
        return None
    m = by_name.get(root.value.id)
    if not m:
        return None
    cols = {f.name: f for f in m.fields if not f.ref}
    parts = []
    for k in keywords:
        lit = _lit(k.value)
        if k.arg not in cols or lit is None:
            return None
        parts.append('"%s" = %s' % (k.arg, lit))
    return " AND ".join(parts)


def match_query(source, models):
    """関数のソース(先頭数行)から ("all" | "count", モデル) を返す。合わなければ None。"""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return None
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if fn is None:
        return None
    body = [b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return None
    by_name = {m.name: m for m in models}
    v = _unwrap(body[0].value)
    # list(M.objects.values()) のように list が内側にある形は _unwrap で外れる
    root, calls = _chain(v)
    ops = [(c[0], c[1], c[2]) for c in calls]
    where = ""
    # Django の filter(列=整数/真偽/文字列) だけは、列が確かにあるときに WHERE へ移す(ほかのキーワード引数つきは移さない)
    if ops and ops[0][0] == "filter" and ops[0][2] and not ops[0][1] and not any(c[2] for c in ops[1:]):
        wm = _where(ops[0][2], root, by_name)
        if wm is None:
            return None
        where = wm
        ops[0] = ("all", [], [])
    if any(c[2] for c in ops):
        return None

    def done(kind, mname):
        m = by_name.get(mname)
        return (kind, m, where) if m else None

    # Django: M.objects.all() / values() / count()
    if isinstance(root, ast.Attribute) and root.attr == "objects" and isinstance(root.value, ast.Name):
        names = [c[0] for c in ops]
        if names in (["all"], ["values"], ["all", "values"]) and all(not c[1] for c in ops):
            return done("all", root.value.id)
        if names in (["count"], ["all", "count"]) and all(not c[1] for c in ops):
            return done("count", root.value.id)
        return None
    # Flask-SQLAlchemy: M.query.all() / count()
    if isinstance(root, ast.Attribute) and root.attr == "query" and isinstance(root.value, ast.Name):
        names = [c[0] for c in ops]
        if names in (["all"], ["count"]) and all(not c[1] for c in ops):
            return done(names[0], root.value.id)
        return None
    # SQLAlchemy: session.query(M).all() / count()、SQLModel: session.exec(select(M)).all()
    if ops and ops[0][0] == "query" and len(ops[0][1]) == 1 and isinstance(ops[0][1][0], (ast.Name, ast.Attribute)):
        rest = [c[0] for c in ops[1:]]
        if rest in (["all"], ["count"]) and all(not c[1] for c in ops[1:]):
            return done(rest[0], _name(ops[0][1][0]))
        return None
    if ops and ops[0][0] == "exec" and len(ops[0][1]) == 1 and isinstance(ops[0][1][0], ast.Call) and _name(ops[0][1][0].func) == "select":
        sel = ops[0][1][0]
        if len(sel.args) == 1 and not sel.keywords and [c[0] for c in ops[1:]] == ["all"] and not ops[1][1]:
            return done("all", _name(sel.args[0]))
    return None


def sql_for(kind, model, where=""):
    w = (" WHERE " + where) if where else ""
    if kind == "count":
        return 'SELECT COUNT(*) FROM "%s"%s' % (model.table, w)
    return 'SELECT * FROM "%s"%s' % (model.table, w)
