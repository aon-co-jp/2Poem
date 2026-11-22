"""Python の関数本体のうち、型を追える計算式・代入・分岐だけを Rust の式にする(実行はしない。ast だけ)。

方針: **誤訳するより移植しない方がよい**。型が決まらない・Python と Rust で結果が変わりうる・実行時エラーになりうるものは
`Unsupported` にして、呼び出し側が従来どおり `TODO` のまま残す。

対応する範囲:
  - 型: 整数(i64)・小数(f64)・文字列(String)・真偽(bool)。パスパラメータの型から始める。
  - 式: + - * / // % **(整数の小さい定数乗)、単項 - と not、比較(1つだけ)、and / or(真偽だけ)、三項 `a if c else b`、
        f 文字列({} のみ。整数と文字列だけ)、文字列の連結、len / abs / min / max / str、文字列の upper / lower / strip。
  - 文: `x = 式`(新しい名前への1回だけの代入)、`if / elif / else`(すべての道が return で終わるもの)、`return`。
  - 応答: dict / list / 定数 / jsonify / JSONResponse / JsonResponse(JSON)、HttpResponse / PlainTextResponse / Response(文字列)。
慎重に外すもの: 変数での割り算・剰余(0 で Python は例外)、文字列の直接 return(フレームワークごとに応答が違う)、連鎖比較、in / is。
"""
import ast

T_INT, T_FLOAT, T_STR, T_BOOL = "i64", "f64", "String", "bool"
NUMERIC = (T_INT, T_FLOAT)
JSON_CALLS = ("jsonify", "JSONResponse", "JsonResponse")
TEXT_CALLS = ("HttpResponse", "PlainTextResponse", "Response")


class Unsupported(Exception):
    pass


def _ident(name):
    from .emit import rust_ident  # 循環 import を避けて遅延 import

    return rust_ident(name)


def _strlit(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def _fmtlit(s):
    return _strlit(s).replace("{", "{{").replace("}", "}}")


def _num(code, t, want):
    """数の型をそろえる(整数を小数へ)。"""
    return "(%s as f64)" % code if (t == T_INT and want == T_FLOAT) else code


def _unify(a, ta, b, tb):
    if ta not in NUMERIC or tb not in NUMERIC:
        raise Unsupported("non numeric")
    t = T_FLOAT if T_FLOAT in (ta, tb) else T_INT
    return _num(a, ta, t), _num(b, tb, t), t


def _nonzero_literal(n):
    return isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool) and n.value != 0


def _pos_int_literal(n):
    return isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool) and n.value > 0


def ex(n, env):
    """式 → (Rust コード, 型)。"""
    if isinstance(n, ast.Constant):
        v = n.value
        if isinstance(v, bool):
            return ("true" if v else "false"), T_BOOL
        if isinstance(v, int):
            if abs(v) > 2**62:
                raise Unsupported("big int")
            return "%d_i64" % v, T_INT
        if isinstance(v, float):
            if v != v or v in (float("inf"), float("-inf")):
                raise Unsupported("nonfinite")
            return "%r_f64" % v, T_FLOAT
        if isinstance(v, str):
            return "%s.to_string()" % _strlit(v), T_STR
        raise Unsupported("constant")
    if isinstance(n, ast.Name):
        if n.id not in env:
            raise Unsupported("name " + n.id)
        t = env[n.id]
        return (_ident(n.id) + (".clone()" if t == T_STR else "")), t
    if isinstance(n, ast.UnaryOp):
        c, t = ex(n.operand, env)
        if isinstance(n.op, ast.USub) and t in NUMERIC:
            return "(-%s)" % c, t
        if isinstance(n.op, ast.Not) and t == T_BOOL:
            return "(!%s)" % c, T_BOOL
        raise Unsupported("unary")
    if isinstance(n, ast.BinOp):
        return _binop(n, env)
    if isinstance(n, ast.Compare):
        if len(n.ops) != 1:
            raise Unsupported("chained compare")
        a, ta = ex(n.left, env)
        b, tb = ex(n.comparators[0], env)
        op = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}.get(type(n.ops[0]))
        if op is None:
            raise Unsupported("compare op")
        if ta in NUMERIC and tb in NUMERIC:
            a, b, _ = _unify(a, ta, b, tb)
        elif ta == tb == T_STR:
            pass
        elif ta == tb == T_BOOL and op in ("==", "!="):
            pass
        else:
            raise Unsupported("compare types")
        return "(%s %s %s)" % (a, op, b), T_BOOL
    if isinstance(n, ast.BoolOp):
        parts = []
        for v in n.values:
            c, t = ex(v, env)
            if t != T_BOOL:
                raise Unsupported("truthiness")
            parts.append(c)
        return "(%s)" % (" && " if isinstance(n.op, ast.And) else " || ").join(parts), T_BOOL
    if isinstance(n, ast.IfExp):
        c, tc = ex(n.test, env)
        a, ta = ex(n.body, env)
        b, tb = ex(n.orelse, env)
        if tc != T_BOOL:
            raise Unsupported("ifexp test")
        if ta in NUMERIC and tb in NUMERIC:
            a, b, t = _unify(a, ta, b, tb)
        elif ta == tb:
            t = ta
        else:
            raise Unsupported("ifexp types")
        return "(if %s { %s } else { %s })" % (c, a, b), t
    if isinstance(n, ast.JoinedStr):
        fmt, args = "", []
        for p in n.values:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                fmt += p.value.replace("{", "{{").replace("}", "}}").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            elif isinstance(p, ast.FormattedValue) and p.conversion == -1 and p.format_spec is None:
                c, t = ex(p.value, env)
                if t not in (T_INT, T_STR):
                    raise Unsupported("fstring type")
                fmt += "{}"
                args.append(c)
            else:
                raise Unsupported("fstring part")
        return "(format!(\"%s\"%s))" % (fmt, "".join(", " + a for a in args)), T_STR
    if isinstance(n, ast.Call):
        return _call(n, env)
    raise Unsupported(type(n).__name__)


def _binop(n, env):
    a, ta = ex(n.left, env)
    b, tb = ex(n.right, env)
    op = n.op
    if isinstance(op, ast.Add) and ta == tb == T_STR:
        return '(format!("{}{}", %s, %s))' % (a, b), T_STR
    if isinstance(op, (ast.Add, ast.Sub, ast.Mult)):
        a, b, t = _unify(a, ta, b, tb)
        return "(%s %s %s)" % (a, {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(op)], b), t
    if isinstance(op, ast.Div):
        if not _nonzero_literal(n.right):
            raise Unsupported("division by non-literal")
        a, b, _ = _unify(a, ta, b, tb)
        a = a if ta == T_FLOAT or tb == T_FLOAT else "(%s as f64)" % a
        b = b if ta == T_FLOAT or tb == T_FLOAT else "(%s as f64)" % b
        return "(%s / %s)" % (a, b), T_FLOAT
    if isinstance(op, ast.FloorDiv):
        if ta == tb == T_INT and _pos_int_literal(n.right):
            return "(%s).div_euclid(%s)" % (a, b), T_INT
        if _nonzero_literal(n.right) and ta in NUMERIC and tb in NUMERIC and T_FLOAT in (ta, tb):
            a, b, _ = _unify(a, ta, b, tb)
            return "((%s / %s).floor())" % (a, b), T_FLOAT
        raise Unsupported("floor division")
    if isinstance(op, ast.Mod):
        if ta == tb == T_INT and _pos_int_literal(n.right):
            return "(%s).rem_euclid(%s)" % (a, b), T_INT
        raise Unsupported("modulo")
    if isinstance(op, ast.Pow):
        if ta == T_INT and isinstance(n.right, ast.Constant) and isinstance(n.right.value, int) and not isinstance(n.right.value, bool) and 0 <= n.right.value <= 16:
            return "(%s).pow(%d)" % (a, n.right.value), T_INT
        raise Unsupported("power")
    raise Unsupported("binop")


def _call(n, env):
    if n.keywords:
        raise Unsupported("keywords")
    if isinstance(n.func, ast.Name):
        f = n.func.id
        args = [ex(a, env) for a in n.args]
        if f == "len" and len(args) == 1 and args[0][1] == T_STR:
            return "(%s.chars().count() as i64)" % args[0][0], T_INT
        if f == "abs" and len(args) == 1 and args[0][1] in NUMERIC:
            return "(%s).abs()" % args[0][0], args[0][1]
        if f in ("min", "max") and len(args) == 2 and args[0][1] == args[1][1] and args[0][1] in NUMERIC:
            # 型が混ざると Python は元の型のまま返す(max(7, 3.5) は 7)ので、同じ型どうしだけ移植する
            return "(%s).%s(%s)" % (args[0][0], f, args[1][0]), args[0][1]
        if f == "str" and len(args) == 1 and args[0][1] in (T_INT, T_STR):
            return "%s.to_string()" % args[0][0], T_STR
        raise Unsupported("call " + f)
    if isinstance(n.func, ast.Attribute) and not n.args:
        c, t = ex(n.func.value, env)
        if t == T_STR and n.func.attr in ("upper", "lower", "strip"):
            m = {"upper": "to_uppercase()", "lower": "to_lowercase()", "strip": "trim().to_string()"}[n.func.attr]
            return "%s.%s" % (c, m), T_STR
    raise Unsupported("call")


def jv(n, env):
    """JSON の値の位置に置く Rust のトークン(serde_json::json! の中身)。"""
    if isinstance(n, ast.Dict):
        out = []
        for k, v in zip(n.keys, n.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                raise Unsupported("dict key")
            out.append("%s: %s" % (_strlit(k.value), jv(v, env)))
        return "{" + ", ".join(out) + "}"
    if isinstance(n, (ast.List, ast.Tuple)):
        return "[" + ", ".join(jv(e, env) for e in n.elts) + "]"
    if isinstance(n, ast.Constant) and n.value is None:
        return "null"
    code, _ = ex(n, env)
    return "(%s)" % code


def _ret(v, env):
    """return の値 → (種類, Rust の値を返す式)。"""
    if isinstance(v, ast.Call) and isinstance(v.func, (ast.Name, ast.Attribute)):
        name = ast.unparse(v.func)
        if name.endswith(JSON_CALLS) and len(v.args) == 1 and all(k.arg in ("safe", "status") for k in v.keywords) and not any(k.arg == "status" for k in v.keywords):
            return "json", "serde_json::json!(%s)" % jv(v.args[0], env)
        if name.endswith(TEXT_CALLS) and len(v.args) == 1 and not v.keywords:
            c, t = ex(v.args[0], env)
            if t != T_STR:
                raise Unsupported("text type")
            return "text", c
        raise Unsupported("return call")
    if isinstance(v, ast.Constant) and isinstance(v.value, str):
        return "text", ex(v, env)[0]
    if isinstance(v, (ast.Dict, ast.List, ast.Tuple)):
        return "json", "serde_json::json!(%s)" % jv(v, env)
    if isinstance(v, ast.Constant) or isinstance(v, (ast.BinOp, ast.IfExp, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.Name)):
        c, t = ex(v, env)
        if t == T_STR:
            raise Unsupported("bare string return")
        return "json", "serde_json::json!(%s)" % jv(v, env)
    raise Unsupported("return value")


def _block(stmts, env, kinds):
    """文の並び → Rust のブロックの中身(最後は値の式)。すべての道が return で終わらなければ Unsupported。"""
    stmts = [s for s in stmts if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    env = dict(env)
    lines = []
    for i, s in enumerate(stmts):
        if isinstance(s, ast.Assign):
            if len(s.targets) != 1 or not isinstance(s.targets[0], ast.Name) or s.targets[0].id in env:
                raise Unsupported("assign")
            code, t = ex(s.value, env)
            lines.append("let %s: %s = %s;" % (_ident(s.targets[0].id), t, code))
            env[s.targets[0].id] = t
        elif isinstance(s, ast.Return):
            if s.value is None:
                raise Unsupported("bare return")
            k, code = _ret(s.value, env)
            kinds.add(k)
            lines.append(code)
            return "\n".join(lines)
        elif isinstance(s, ast.If):
            cond, tc = ex(s.test, env)
            if tc != T_BOOL:
                raise Unsupported("if truthiness")
            after = list(stmts[i + 1 :])  # 分岐のあとに続く文は、return で終わらなかった道が引き継ぐ
            body = _block(list(s.body) + after, env, kinds)
            other = _block(list(s.orelse) + after, env, kinds)
            lines.append("if %s {\n%s\n} else {\n%s\n}" % (cond, body, other))
            return "\n".join(lines)
        else:
            raise Unsupported(type(s).__name__)
    raise Unsupported("falls through")


def port_complex(func, param_types):
    """関数本体 → (種類, 値を返す Rust のブロック式)。移植できなければ ("", "")。"""
    if len(func.body) == 0:
        return "", ""
    env = {k: v for k, v in param_types.items() if v in (T_INT, T_FLOAT, T_STR, T_BOOL)}
    kinds = set()
    try:
        body = _block(func.body, env, kinds)
    except Unsupported:
        return "", ""
    if len(kinds) != 1:
        return "", ""
    return kinds.pop(), "{\n" + body + "\n}"
