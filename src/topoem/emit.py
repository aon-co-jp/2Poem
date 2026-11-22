"""抽出したルートから Rust(Poem または RPoem)のプロジェクトを生成する。

生成物は **骨組み**: ルーティング・パスパラメータの型・ハンドラ名・メソッドを写し、各ハンドラの本体には元の Python の先頭数行を
コメントで添えて `TODO` の応答を返す。本体(業務ロジック)は自動では移植しない。
"""
import os
import re

from .extract import Route

DEFAULT_POEM = "3"
RPOEM_REL = "../RPoem"
CPU_LINE = "    // 起動時にCPUの命令セット(AVX-512 / AVX2 / NEON など)を検出して表示する(open-cpu。--no-open-cpu で外せる)\n    println!(\"{}\", open_cpu::runtime_report());\n"


def choose_target(frameworks):
    """自動選択: Django(管理・ORM・認証まで含む重い構成)は RPoem(アプリケーションサーバー)、Flask/FastAPI は Poem。"""
    return "rpoem" if "django" in frameworks else "poem"


def rust_ident(name):
    s = re.sub(r"\W", "_", name).lower().strip("_") or "handler"
    if s[0].isdigit():
        s = "h_" + s
    if s in ("fn", "let", "mut", "type", "match", "loop", "move", "ref", "self", "use", "mod", "crate", "super", "where", "async", "await", "dyn", "impl", "trait", "struct", "enum", "const", "static", "pub", "in", "for", "if", "else", "while", "return", "break", "continue", "as", "true", "false", "unsafe", "box", "do", "final", "macro", "override", "priv", "try", "typeof", "unsized", "virtual", "yield", "abstract", "become"):
        s += "_"
    return s


def _comment(route):
    lines = ["/// 元: %s:%d (%s)" % (os.path.basename(route.file), route.line, route.framework)]
    for ln in route.source.splitlines()[:8]:
        lines.append("/// > " + ln.replace("\r", "").rstrip())
    for n in route.notes:
        lines.append("/// 注意: " + n)
    return "\n".join(lines)


def _unique(routes):
    """(メソッド, パス) ごとに一意なハンドラ名を付ける。"""
    seen, out = {}, []
    for r in routes:
        for m in r.methods:
            base = rust_ident(r.handler)
            name = base if base not in seen else "%s_%s" % (base, m)
            i = 2
            while name in seen:
                name = "%s_%s_%d" % (base, m, i)
                i += 1
            seen[name] = True
            out.append((r, m, name))
    return out


def _group_by_path(items):
    g = {}
    for r, m, name in items:
        g.setdefault(r.path, []).append((m, name))
    return g


def _poem_handler(r, name):
    args = ""
    if len(r.params) == 1:
        p = r.params[0]
        args = "Path(%s): Path<%s>" % (rust_ident(p.name), p.rust)
    elif len(r.params) > 1:
        args = "Path((%s)): Path<(%s)>" % (", ".join(rust_ident(p.name) for p in r.params), ", ".join(p.rust for p in r.params))
    if r.ported_expr:
        note = "    // 自動移植: 計算式・代入・分岐を Rust にした(型を追えた範囲だけ。整数は i64、Python の大きい整数とは違う)\n"
        if r.ported_kind == "text":
            return "%s\n#[handler]\nasync fn %s(%s) -> String {\n%s    %s\n}\n" % (_comment(r), name, args, note, r.ported_expr)
        return "%s\n#[handler]\nasync fn %s(%s) -> Json<Value> {\n%s    Json(%s)\n}\n" % (_comment(r), name, args, note, r.ported_expr)
    if r.ported and r.ported_kind == "text":
        return "%s\n#[handler]\nasync fn %s(%s) -> String {\n    // 自動移植: 元の本体は定数/パラメータだけを返す単純な関数\n    %s.to_string()\n}\n" % (_comment(r), name, args, r.ported)
    if r.ported:
        return "%s\n#[handler]\nasync fn %s(%s) -> Json<Value> {\n    // 自動移植: 元の本体は定数/パラメータだけを返す単純な関数\n    Json(json!(%s))\n}\n" % (_comment(r), name, args, r.ported)
    pj = ", ".join('"%s": %s' % (p.name, rust_ident(p.name)) for p in r.params)
    body = 'Json(json!({"todo": "%s"%s}))' % (name, (", " + pj) if pj else "")
    return "%s\n#[handler]\nasync fn %s(%s) -> Json<Value> {\n    // TODO: Python の本体を移植する(戻りの種類の推定: %s)\n    %s\n}\n" % (_comment(r), name, args, r.returns, body)


def _rpoem_view_handler(r, name):
    """画面を返すハンドラ: HTML は手書きせず、Rust の VNode を SSR して返す(JavaScript・CSS を含めない)。"""
    lines = []
    for p in r.params:
        ident = rust_ident(p.name)
        lines.append('    let %s: %s = params.get("%s").and_then(|s| s.parse().ok()).unwrap_or_default();' % (ident, p.rust, p.name))
    shown = "".join('.child(open_runo_view::h("p").child(format!("%s = {}", %s)).build())' % (p.name, rust_ident(p.name)) for p in r.params)
    unused = "params" if r.params else "_params"
    return (
        "%s\n#[open_runo_poem_compat_macro::handler]\nasync fn %s(_req: Request, %s: Params) -> Response {\n%s\n"
        "    // TODO: Python のテンプレートの内容を、open-runo-view の VNode として書き直す(HTML・CSS・JavaScript は手書きしない)\n"
        "    let body = open_runo_view::h(\"main\").child(open_runo_view::h(\"h1\").child(\"%s\").build())%s.build();\n"
        "    let mut page = open_runo_view::ssr::SsrPage::default_for(&body);\n    page.title = \"%s\";\n"
        "    open_runo_poem_compat::hyper_compat::html_response(StatusCode::OK, open_runo_view::ssr::render_page(&page))\n}\n"
    ) % (_comment(r), name, unused, "\n".join(lines), name, shown, name)


def _rpoem_handler(r, name):
    if r.returns == "html":
        return _rpoem_view_handler(r, name)
    lines = []
    for p in r.params:
        ident = rust_ident(p.name)
        lines.append('    let %s: %s = params.get("%s").and_then(|s| s.parse().ok()).unwrap_or_default();' % (ident, p.rust, p.name))
    pj = ", ".join('"%s": %s' % (p.name, rust_ident(p.name)) for p in r.params)
    unused = "params" if r.params else "_params"
    if r.ported_expr:
        if r.ported_kind == "text":
            body = "open_runo_poem_compat::hyper_compat::html_response(StatusCode::OK, %s)" % r.ported_expr
        else:
            body = "Json(%s).into_response(StatusCode::OK)" % r.ported_expr
        return "%s\n#[open_runo_poem_compat_macro::handler]\nasync fn %s(_req: Request, %s: Params) -> Response {\n%s\n    // 自動移植: 計算式・代入・分岐を Rust にした(型を追えた範囲だけ。整数は i64)\n    %s\n}\n" % (_comment(r), name, unused, "\n".join(lines), body)
    if r.ported and r.ported_kind == "text":
        body = 'open_runo_poem_compat::hyper_compat::html_response(StatusCode::OK, %s.to_string())' % r.ported
        return "%s\n#[open_runo_poem_compat_macro::handler]\nasync fn %s(_req: Request, %s: Params) -> Response {\n%s\n    // 自動移植: 元の本体は定数/パラメータだけを返す単純な関数\n    %s\n}\n" % (_comment(r), name, unused, "\n".join(lines), body)
    if r.ported:
        body = 'Json(serde_json::json!(%s)).into_response(StatusCode::OK)' % r.ported
        return "%s\n#[open_runo_poem_compat_macro::handler]\nasync fn %s(_req: Request, %s: Params) -> Response {\n%s\n    // 自動移植: 元の本体は定数/パラメータだけを返す単純な関数\n    %s\n}\n" % (_comment(r), name, unused, "\n".join(lines), body)
    body = 'Json(serde_json::json!({"todo": "%s"%s})).into_response(StatusCode::OK)' % (name, (", " + pj) if pj else "")
    return "%s\n#[open_runo_poem_compat_macro::handler]\nasync fn %s(_req: Request, %s: Params) -> Response {\n%s\n    // TODO: Python の本体を移植する(戻りの種類の推定: %s)\n    %s\n}\n" % (_comment(r), name, unused, "\n".join(lines), r.returns, body)


def render_main(routes, target, warnings_out, open_cpu=True):
    items = _unique(routes)
    groups = _group_by_path(items)
    by_name = {name: r for r, m, name in items}
    verbs = {"get", "post", "put", "delete", "patch", "head"}
    if target == "rpoem":
        verbs = {"get", "post", "put", "delete", "head"}
    used = sorted({m for _, m, _ in items if m in verbs} | {"get"})
    handlers = []
    for r, m, name in items:
        handlers.append(_rpoem_handler(r, name) if target == "rpoem" else _poem_handler(r, name))
    route_lines = []
    for path, ms in groups.items():
        expr = None
        for m, name in ms:
            if m not in verbs:
                if m == "patch" and target == "rpoem":
                    warnings_out.append("%s: PATCH は RPoem の互換面に無いため PUT として登録しました(要確認)" % path)
                    m = "put"
                elif m == "options":
                    continue  # CORS で扱う
                else:
                    warnings_out.append("%s: %s は未対応のため飛ばしました" % (path, m.upper()))
                    continue
            call = "%s(%s%s)" % (m, name, "()" if target == "rpoem" else "")
            expr = call if expr is None else "%s.%s(%s%s)" % (expr, m, name, "()" if target == "rpoem" else "")
        if expr:
            route_lines.append('        .at("%s", %s)' % (path, expr))
    if target == "rpoem":
        head = "use open_runo_poem_compat::{%s, Json, Params, Request, Response, Route, Server, StatusCode, TcpListener};\n" % ", ".join(sorted(set(used)))
        tail = (
            "\n#[tokio::main]\nasync fn main() {\n    let app = Route::new()\n%s;\n"
            "%s    let (addr, handle) = Server::new(TcpListener::bind(([0, 0, 0, 0], 3000))).run(app).await.expect(\"bind :3000\");\n"
            "    println!(\"listening on {addr}\");\n    handle.await.ok();\n}\n"
        ) % ("\n".join(route_lines), CPU_LINE if open_cpu else "")
    else:
        head = "use poem::{%s, handler, listener::TcpListener, web::{Json, Path}, Route, Server};\nuse serde_json::{json, Value};\n" % ", ".join(sorted(set(used)))
        tail = "\n#[tokio::main]\nasync fn main() -> Result<(), std::io::Error> {\n%s    let app = Route::new()\n%s;\n    Server::new(TcpListener::bind(\"0.0.0.0:3000\")).run(app).await\n}\n" % (CPU_LINE if open_cpu else "", "\n".join(route_lines))
    if not routes:
        tail = tail.replace("Route::new()\n;", "Route::new();")
    # 使われていない Path の import による警告を避ける
    text = "//! 2Poem が生成した骨組み(%s)。ルーティングとパスパラメータの型は写してあり、単純な本体(定数・パラメータ・計算式・代入・分岐)は自動移植、それ以外のハンドラの本体は TODO。\n#![allow(unused_variables, unused_imports, unused_parens)]\n%s\n%s%s" % (target, head, "\n".join(handlers), tail)
    return text, by_name


OPEN_CPU_DEP = 'open-cpu = { git = "https://github.com/aon-co-jp/open-cpu" }\n'


def render_cargo(name, target, poem_version, rpoem_path, open_cpu=True, models=False):
    if target == "rpoem":
        deps = 'open-runo-poem-compat = { path = "%s/crates/open-runo-poem-compat" }\nopen-runo-poem-compat-macro = { path = "%s/crates/open-runo-poem-compat-macro" }\nopen-runo-view = { path = "%s/crates/open-runo-view" }\nserde_json = "1"\n' % (rpoem_path, rpoem_path, rpoem_path)
    else:
        deps = 'poem = "%s"\nserde_json = "1"\n' % poem_version
    if open_cpu:
        deps += OPEN_CPU_DEP
    if models:
        deps += 'serde = { version = "1", features = ["derive"] }\n'
    return '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n%stokio = { version = "1", features = ["rt-multi-thread", "macros", "net"] }\n' % (name, deps)


def generate(routes, out_dir, target, name="converted-app", poem_version=DEFAULT_POEM, rpoem_path=RPOEM_REL, source_root=None, open_cpu=True):
    """プロジェクトを書き出す。警告(文字列のリスト)を返す。"""
    warnings = []
    os.makedirs(os.path.join(out_dir, "src"), exist_ok=True)
    from . import orm

    models = orm.extract_models(source_root) if source_root else []
    for r in routes:
        for m in models:
            if re.search(r"\b%s\b" % re.escape(m.name), r.source or ""):
                r.notes.append("ORM: モデル %s(テーブル %s)を使う。構造体は src/models.rs、SQL は schema.sql。問い合わせは自動では移植しない" % (m.name, m.table))
    main, _ = render_main(routes, target, warnings, open_cpu)
    if models:
        main = main.replace("]\nuse ", "]\nmod models;\nuse ", 1)
        with open(os.path.join(out_dir, "src", "models.rs"), "w", encoding="utf-8", newline="\n") as f:
            f.write(orm.render_models_rs(models))
        with open(os.path.join(out_dir, "schema.sql"), "w", encoding="utf-8", newline="\n") as f:
            f.write(orm.render_schema_sql(models))
        warnings.append("ORM のモデル %d 件を src/models.rs と schema.sql に写しました(問い合わせは自動では移植しません)" % len(models))
    with open(os.path.join(out_dir, "Cargo.toml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_cargo(name, target, poem_version, rpoem_path, open_cpu, bool(models)))
    with open(os.path.join(out_dir, "src", "main.rs"), "w", encoding="utf-8", newline="\n") as f:
        f.write(main)
    with open(os.path.join(out_dir, ".gitignore"), "w", encoding="utf-8", newline="\n") as f:
        f.write("target/\n")
    if target == "rpoem" and source_root:
        from . import frontend

        base = source_root if os.path.isdir(source_root) else (os.path.dirname(source_root) or ".")
        report = frontend.render_report(frontend.scan_frontend(source_root), base)
        if report:
            with open(os.path.join(out_dir, "MIGRATION.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(report)
            warnings.append("画面まわりの資産(JavaScript/TypeScript/React/Bootstrap/Cosmo/HTML/CSS)はコピーせず MIGRATION.md に記録しました")
    return warnings
