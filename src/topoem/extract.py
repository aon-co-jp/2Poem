"""Flask / FastAPI / Django のルートを Python のソースから取り出す(実行はしない。ast だけ)。"""
import ast
import os
import re
from dataclasses import dataclass, field

from .detect import detect_framework, parse_source
from .port import port_complex

HTTP = ("get", "post", "put", "delete", "patch", "head", "options")
RUST_TYPES = {"int": "i64", "float": "f64", "str": "String", "string": "String", "path": "String", "uuid": "String", "slug": "String", "bool": "bool"}


@dataclass
class Param:
    name: str
    rust: str = "String"
    wildcard: bool = False


@dataclass
class Route:
    framework: str
    methods: list
    path: str  # 正規化済み: /items/:id (ワイルドカードは /files/*rest)
    handler: str
    params: list = field(default_factory=list)
    source: str = ""  # 元の関数のソース(先頭数行、コメントに使う)
    file: str = ""
    line: int = 0
    returns: str = "json"  # json | html | text | unknown
    notes: list = field(default_factory=list)
    ported: str = ""  # 自動で移植できた応答(Rust の json! 式)。空なら TODO のまま
    ported_kind: str = ""  # json | text
    ported_expr: str = ""  # 計算式・分岐・代入まで含めて移植した、値(Value / String)を返す Rust のブロック式


def _const_str(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def _flask_path(raw):
    """/items/<int:id>/<name> → (/items/:id/:name, [Param...])"""
    params = []

    def rep(m):
        conv, name = (m.group(1) or "string"), m.group(2)
        conv = conv.rstrip(":")
        wild = conv == "path"
        params.append(Param(name, RUST_TYPES.get(conv, "String"), wild))
        return ("*" if wild else ":") + name

    return re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>", rep, raw), params


def _fastapi_path(raw, func):
    ann = {}
    for a in func.args.args + func.args.kwonlyargs:
        if a.annotation is not None and isinstance(a.annotation, ast.Name):
            ann[a.arg] = a.annotation.id
    params = []

    def rep(m):
        name = m.group(1).split(":")[0]
        wild = m.group(1).endswith(":path")
        params.append(Param(name, RUST_TYPES.get(ann.get(name, "str"), "String"), wild))
        return ("*" if wild else ":") + name

    return re.sub(r"\{([^}]+)\}", rep, raw), params


def _django_path(raw):
    """<int:id>/ や (?P<id>\\d+) を /:id にする。先頭の ^ と末尾の $ は落とす。"""
    params = []

    def rep_conv(m):
        conv, name = (m.group(1) or "str"), m.group(2)
        wild = conv == "path"
        params.append(Param(name, RUST_TYPES.get(conv, "String"), wild))
        return ("*" if wild else ":") + name

    p = raw

    def rep_re(m):
        name = m.group(1)
        t = "i64" if r"\d" in m.group(2) else "String"
        params.append(Param(name, t))
        return ":" + name

    p = re.sub(r"\(\?P<(\w+)>([^)]*)\)", rep_re, p)
    p = re.sub(r"<(?:(\w+):)?(\w+)>", rep_conv, p)
    p = p.lstrip("^").rstrip("$")
    path = "/" + p.strip("/")
    params.sort(key=lambda q: path.find(":" + q.name) if path.find(":" + q.name) >= 0 else path.find("*" + q.name))
    return path, params


def _returns_kind(func):
    kinds = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Return) and n.value is not None:
            v = n.value
            if isinstance(v, ast.Dict):
                kinds.add("json")
            elif isinstance(v, ast.Call):
                nm = ast.unparse(v.func)
                if nm.endswith(("jsonify", "JSONResponse", "JsonResponse")):
                    kinds.add("json")
                elif nm.endswith(("render_template", "render", "TemplateResponse", "HTMLResponse")):
                    kinds.add("html")
                elif nm.endswith(("HttpResponse", "PlainTextResponse", "Response")):
                    kinds.add("text")
            elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                kinds.add("text")
            elif isinstance(v, (ast.List, ast.Name, ast.Attribute, ast.Subscript)):
                kinds.add("json")
    return kinds.pop() if len(kinds) == 1 else ("json" if "json" in kinds else "unknown")


def _ident(name):
    from .emit import rust_ident  # 循環 import を避けて遅延 import

    return rust_ident(name)


def _lit(n, names):
    """Python の式(定数・パラメータ名・それらの dict/list)を Rust の json! の中身にする。できなければ None。"""
    if isinstance(n, ast.Constant):
        v = n.value
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        return None
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant) and isinstance(n.operand.value, (int, float)) and not isinstance(n.operand.value, bool):
        return "-" + repr(n.operand.value)
    if isinstance(n, ast.Name) and n.id in names:
        return _ident(n.id)
    if isinstance(n, (ast.List, ast.Tuple)):
        items = [_lit(e, names) for e in n.elts]
        return None if any(i is None for i in items) else "[" + ", ".join(items) + "]"
    if isinstance(n, ast.Dict):
        out = []
        for k, v in zip(n.keys, n.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                return None
            lv = _lit(v, names)
            if lv is None:
                return None
            out.append("%s: %s" % (_lit(k, names), lv))
        return "{" + ", ".join(out) + "}"
    return None


def port_body(func, params):
    """params: {名前: Rust の型}。まず定数だけの単純な本体を、だめなら計算式・代入・分岐を移植する。
    戻り値は (種類, 単純な本体の json! の中身, 複雑な本体のブロック式)。"""
    kind, lit = _port_simple(func, set(params))
    if lit:
        return kind, lit, ""
    kind, expr = port_complex(func, params)
    return kind, "", expr


def _port_simple(func, param_names):
    """本体が(docstring を除いて)単一の return で、その値が定数・パラメータ・dict/list だけなら (種類, Rust 式)。それ以外は ("", "")。"""
    body = [b for b in func.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return "", ""
    v = body[0].value
    if isinstance(v, ast.Call) and v.args and ast.unparse(v.func).endswith(("jsonify", "JSONResponse", "JsonResponse")):
        v = v.args[0]
    elif isinstance(v, ast.Call) and v.args and ast.unparse(v.func).endswith(("HttpResponse", "PlainTextResponse", "Response")):
        t = _lit(v.args[0], param_names)
        return ("text", t) if t and t.startswith('"') else ("", "")
    lit = _lit(v, param_names)
    if lit is None:
        return "", ""
    return ("text" if isinstance(v, ast.Constant) and isinstance(v.value, str) else "json"), lit


def _snippet(src_lines, func, limit=8):
    seg = src_lines[func.lineno - 1 : min(func.end_lineno, func.lineno - 1 + limit)]
    return "\n".join(seg)


def _decorator_routes(tree, src, file):
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args):
                continue
            attr, raw = d.func.attr, _const_str(d.args[0])
            if raw is None:
                continue
            if attr == "route":
                methods = ["get"]
                for kw in d.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [_const_str(e).lower() for e in kw.value.elts if _const_str(e)] or methods
                fw = "flask"
            elif attr in HTTP:
                methods, fw = [attr], None
            else:
                continue
            is_fastapi = fw is None and ("{" in raw or any(k.arg in ("response_model", "status_code") for k in d.keywords))
            if fw == "flask" or (fw is None and "<" in raw and not is_fastapi):
                path, params = _flask_path(raw)
                fw = "flask"
            else:
                path, params = _fastapi_path(raw, node)
                fw = "fastapi" if fw is None else fw
            if not path.startswith("/"):
                path = "/" + path
            r = Route(fw, methods, path, node.name, params, _snippet(lines, node), file, node.lineno, _returns_kind(node))
            r.ported_kind, r.ported, r.ported_expr = port_body(node, {p.name: p.rust for p in params})
            out.append(r)
    return out


def _django_routes(tree, src, file, views):
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("path", "re_path", "url") and len(node.args) >= 2):
            continue
        raw = _const_str(node.args[0])
        if raw is None:
            continue
        target = node.args[1]
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr == "as_view":
            name = ast.unparse(target.func.value).split(".")[-1]
            methods = views.get(name, {}).get("methods", ["get", "post"])
            snippet, kind = views.get(name, {}).get("source", ""), "json"
        elif isinstance(target, (ast.Name, ast.Attribute)):
            name = ast.unparse(target).split(".")[-1]
            methods, snippet, kind = ["get", "post"], views.get(name, {}).get("source", ""), views.get(name, {}).get("returns", "json")
        else:
            continue  # include(...) などは追わない
        path, params = _django_path(raw)
        r = Route("django", methods, path, name, params, snippet, file, node.lineno, kind)
        fn = views.get(name, {}).get("func")
        if fn is not None and not isinstance(target, ast.Call):
            r.ported_kind, r.ported, r.ported_expr = port_body(fn, {p.name: p.rust for p in params})
        if isinstance(target, ast.Call) or node.func.id == "re_path":
            r.notes.append("Django の正規表現/クラスベースビューは、メソッドとパスを推定しました")
        out.append(r)
    return out


def _collect_views(tree, src):
    lines = src.splitlines()
    views = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            views[node.name] = {"source": _snippet(lines, node), "returns": _returns_kind(node), "func": node}
        elif isinstance(node, ast.ClassDef):
            ms = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in HTTP]
            views[node.name] = {"methods": ms or ["get"], "source": _snippet(lines, node), "returns": "json"}
    return views


def extract_file(path):
    """1ファイルから (ルート一覧, 警告, 判定したフレームワーク, py2だったか)。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    tree, warnings, py2 = parse_source(src)
    fws = detect_framework(tree)
    routes = _decorator_routes(tree, src, path)
    return routes, warnings, fws, py2, tree, src


def extract_project(root):
    """ファイルまたはディレクトリから全ルートを集める。(ルート, 警告, フレームワーク一覧, py2 のファイル一覧)"""
    files = []
    if os.path.isfile(root):
        files = [root]
    else:
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x not in ("venv", ".venv", "node_modules", "__pycache__", ".git", "site-packages", "migrations")]
            files += [os.path.join(d, f) for f in fs if f.endswith(".py")]
    routes, warnings, fws, py2_files = [], [], [], []
    parsed = {}
    for f in sorted(files):
        try:
            r, w, fw, py2, tree, src = extract_file(f)
        except (SyntaxError, ValueError) as e:
            warnings.append("%s: 読めません(%s)" % (f, e))
            continue
        parsed[f] = (tree, src)
        routes += r
        warnings += ["%s: %s" % (f, x) for x in w]
        fws += [x for x in fw if x not in fws]
        if py2:
            py2_files.append(f)
    # Django: urls.py と views.py(同じフォルダ)
    for f, (tree, src) in parsed.items():
        if os.path.basename(f) == "urls.py":
            vf = os.path.join(os.path.dirname(f), "views.py")
            views = _collect_views(*parsed[vf]) if vf in parsed else {}
            routes += _django_routes(tree, src, f, views)
            if "django" not in fws:
                fws.append("django")
    return routes, warnings, fws, py2_files
