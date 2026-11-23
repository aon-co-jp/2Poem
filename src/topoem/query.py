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
    pks = [f.name for f in m.fields if f.pk]
    parts = []
    for k in keywords:
        if k.arg == "pk" and len(pks) == 1:
            k = ast.keyword(arg=pks[0], value=k.value)
        lit = _lit(k.value)
        name, _, lookup = (k.arg or "").partition("__")
        op = {"": "=", "exact": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(lookup)
        if name not in cols or lit is None or op is None:
            return None
        parts.append('"%s" %s %s' % (name, op, lit))
    return " AND ".join(parts)


def _slice_limit(v):
    sl = v.slice
    if isinstance(sl, ast.Slice) and sl.lower is None and sl.step is None and isinstance(sl.upper, ast.Constant)             and type(sl.upper.value) is int and 0 < sl.upper.value <= 10000:
        return sl.upper.value
    return 0


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
    limit = 0
    if isinstance(v, ast.Subscript) and _slice_limit(v):
        limit = _slice_limit(v)
        v = v.value
    # list(M.objects.values()) のように list が内側にある形は _unwrap で外れる
    root, calls = _chain(v)
    if not limit and isinstance(root, ast.Subscript) and _slice_limit(root):
        limit = _slice_limit(root)
        root, inner = _chain(root.value)
        calls = inner + calls
    ops = [(c[0], c[1], c[2]) for c in calls]
    where = ""
    # Django の filter(列=整数/真偽/文字列) だけは、列が確かにあるときに WHERE へ移す(ほかのキーワード引数つきは移さない)
    one = bool(ops and ops[0][0] == "get" and len(ops) == 1 and ops[0][2] and not ops[0][1])
    if ops and ops[0][0] in ("filter", "get") and (ops[0][0] == "filter" or one) and ops[0][2] and not ops[0][1] and not any(c[2] for c in ops[1:]):
        wm = _where(ops[0][2], root, by_name)
        if wm is None:
            return None
        where = wm
        ops[0] = ("all", [], [])
        if one:
            return (("one", by_name[root.value.id], where, "", 0)) if root.value.id in by_name else None
    order = ""
    for i, c in enumerate(ops):
        if c[0] == "order_by" and not c[2] and c[1] and isinstance(root, ast.Attribute) and root.attr == "objects" and isinstance(root.value, ast.Name):
            m = by_name.get(root.value.id)
            cols = {f.name for f in m.fields if not f.ref} if m else set()
            parts = []
            for a in c[1]:
                v = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                if v is None or v.lstrip("-") not in cols:
                    return None
                parts.append('"%s"%s' % (v.lstrip("-"), " DESC" if v.startswith("-") else ""))
            order = ", ".join(parts)
            ops.pop(i)
            break
    # SQLAlchemy / Flask-SQLAlchemy の filter_by(列=リテラル)
    for i, c in enumerate(ops):
        if c[0] == "filter_by" and c[2] and not c[1] and not where:
            mname = ""
            if isinstance(root, ast.Attribute) and root.attr == "query" and isinstance(root.value, ast.Name):
                mname = root.value.id
            elif ops[0][0] == "query" and len(ops[0][1]) == 1:
                mname = _name(ops[0][1][0])
            m = by_name.get(mname)
            if not m:
                return None
            wm = _where(c[2], ast.Attribute(value=ast.Name(id=mname), attr="objects"), by_name)
            if wm is None:
                return None
            where = wm
            ops.pop(i)
            break
    # SQLAlchemy / Flask-SQLAlchemy の order_by(M.列 / M.列.desc() / M.列.asc())
    for i, c in enumerate(ops):
        if c[0] == "order_by" and not c[2] and c[1] and not order:
            mname = ""
            if isinstance(root, ast.Attribute) and root.attr == "query" and isinstance(root.value, ast.Name):
                mname = root.value.id
            elif ops[0][0] == "query" and len(ops[0][1]) == 1:
                mname = _name(ops[0][1][0])
            m = by_name.get(mname)
            if not m:
                return None
            cols = {f.name for f in m.fields if not f.ref}
            parts = []
            for a in c[1]:
                desc = ""
                if isinstance(a, ast.Call) and not a.args and not a.keywords and isinstance(a.func, ast.Attribute) and a.func.attr in ("desc", "asc"):
                    desc = " DESC" if a.func.attr == "desc" else ""
                    a = a.func.value
                if not (isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == mname and a.attr in cols):
                    return None
                parts.append('"%s"%s' % (a.attr, desc))
            order = ", ".join(parts)
            ops.pop(i)
            break
    # SQLAlchemy / Flask-SQLAlchemy の limit(整数)
    for i, c in enumerate(ops):
        if c[0] == "limit" and not c[2] and len(c[1]) == 1 and isinstance(c[1][0], ast.Constant) and type(c[1][0].value) is int and 0 < c[1][0].value <= 10000 and not limit                 and (ops[0][0] == "query" or (isinstance(root, ast.Attribute) and root.attr == "query")):
            limit = c[1][0].value
            ops.pop(i)
            break
    if any(c[2] for c in ops):
        return None

    def done(kind, mname):
        m = by_name.get(mname)
        return (kind, m, where, order if kind == "all" else "", limit if kind == "all" else 0) if m else None

    # Django: M.objects.all() / values() / count()
    if isinstance(root, ast.Attribute) and root.attr == "objects" and isinstance(root.value, ast.Name):
        names = [c[0] for c in ops]
        if (names in (["all"], ["values"], ["all", "values"]) or (names == [] and order)) and all(not c[1] for c in ops):
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
    # SQLModel: session.exec(select(M).where(M.列 == リテラル, ...)).all()
    if ops and ops[0][0] == "exec" and len(ops[0][1]) == 1 and [c[0] for c in ops[1:]] == ["all"] and not ops[1][1]:
        inner = ops[0][1][0]
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "where" and not inner.keywords and inner.args:
            sel = inner.func.value
            if isinstance(sel, ast.Call) and _name(sel.func) == "select" and len(sel.args) == 1 and not sel.keywords:
                m = by_name.get(_name(sel.args[0]))
                if m:
                    cols = {f.name for f in m.fields if not f.ref}
                    parts = []
                    for cond in inner.args:
                        if not (isinstance(cond, ast.Compare) and len(cond.ops) == 1 and isinstance(cond.left, ast.Attribute)
                                and isinstance(cond.left.value, ast.Name) and cond.left.value.id == m.name and cond.left.attr in cols):
                            return None
                        op = {ast.Eq: "=", ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<="}.get(type(cond.ops[0]))
                        lit = _lit(cond.comparators[0])
                        if op is None or lit is None:
                            return None
                        parts.append('"%s" %s %s' % (cond.left.attr, op, lit))
                    where = " AND ".join(parts)
                    return ("all", m, where, "", 0)
    return None


def sql_for(kind, model, where="", order="", limit=0):
    w = (" WHERE " + where) if where else ""
    if order:
        w += " ORDER BY " + order
    if limit:
        w += " LIMIT %d" % limit
    if kind == "count":
        return 'SELECT COUNT(*) FROM "%s"%s' % (model.table, w)
    return 'SELECT * FROM "%s"%s%s' % (model.table, w, " LIMIT 1" if kind == "one" else "")
