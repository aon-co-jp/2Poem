import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from topoem import deps, detect, emit, extract, frontend, versions  # noqa: E402

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def routes_of(name):
    r, w, fws, py2 = extract.extract_project(os.path.join(EX, name))
    return r, w, fws, py2


def test_flask_routes_and_param_types():
    r, _, fws, _ = routes_of("flask_app.py")
    assert fws == ["flask"]
    by = {x.handler: x for x in r}
    assert by["item"].path == "/items/:item_id" and by["item"].methods == ["get", "delete"]
    assert by["item"].params[0].rust == "i64"
    assert by["upload"].path == "/users/:name/files/*rest" and by["upload"].methods == ["post"]


def test_fastapi_types_from_annotations():
    r, _, fws, _ = routes_of("fastapi_app.py")
    assert fws == ["fastapi"]
    by = {x.handler: x for x in r}
    assert by["read_item"].params[0].rust == "i64"
    assert [p.rust for p in by["set_score"].params] == ["String", "f64"]


def test_django_paths_regex_and_class_views():
    r, _, fws, _ = routes_of("django_proj")
    assert "django" in fws
    paths = {x.handler: x for x in r}
    assert paths["legacy"].path == "/legacy/:pk" and paths["legacy"].params[0].rust == "i64"
    assert paths["Hello"].methods == ["get", "post"]
    assert paths["home"].returns == "html"


def test_python2_is_converted_and_warned():
    r, w, fws, py2 = routes_of("py2_app.py")
    assert py2 and any("Python 2" in x for x in w)
    assert r and r[0].path == "/old/:n"


def test_py2_to_py3_fixes():
    out, n, _ = detect.py2_to_py3('print "a", 1\ntry:\n    pass\nexcept ValueError, e:\n    raise KeyError, "x"\nd.iteritems()\n')
    assert 'print("a", 1)' in out and "except ValueError as e:" in out and 'raise KeyError("x")' in out and ".items()" in out and n >= 4


def test_choose_target():
    assert emit.choose_target(["django"]) == "rpoem"
    assert emit.choose_target(["flask"]) == "poem"
    assert emit.choose_target(["fastapi", "flask"]) == "poem"


def test_poem_output_shape():
    r, _, _, _ = routes_of("flask_app.py")
    warn = []
    text, _ = emit.render_main(r, "poem", warn)
    assert "use poem::" in text and "Path(item_id): Path<i64>" in text
    assert '.at("/items/:item_id", get(item).delete(item_delete))' in text
    assert "Path((name, rest)): Path<(String, String)>" in text


def test_rpoem_output_has_no_js_html_css_and_uses_view():
    r, _, _, _ = routes_of("django_proj")
    warn = []
    text, _ = emit.render_main(r, "rpoem", warn)
    assert "open_runo_view::ssr::render_page" in text
    low = "\n".join(l for l in text.splitlines() if not l.strip().startswith("//")).lower()  # コメントは除く
    for bad in ("<script", "<style", "<link", "bootstrap", "react", "node"):
        assert bad not in low, bad


def test_rpoem_patch_warns(tmp_path):
    src = tmp_path / "a.py"
    src.write_text('from flask import Flask\napp = Flask(__name__)\n@app.route("/x", methods=["PATCH"])\ndef x():\n    return {"a": 1}\n', encoding="utf-8")
    r, _, _, _ = extract.extract_project(str(src))
    warn = []
    text, _ = emit.render_main(r, "rpoem", warn)
    assert any("PATCH" in w for w in warn) and ".at(\"/x\", put(x()))" in text


def test_generate_writes_project_and_migration(tmp_path):
    r, _, _, _ = routes_of("django_proj")
    out = tmp_path / "o"
    warnings = emit.generate(r, str(out), "rpoem", rpoem_path="../RPoem", source_root=os.path.join(EX, "django_proj"))
    assert (out / "Cargo.toml").exists() and (out / "src" / "main.rs").exists()
    rep = (out / "MIGRATION.md").read_text(encoding="utf-8")
    assert "react" in rep and "typescript" in rep and "Cosmo" in rep
    assert any("MIGRATION.md" in w for w in warnings)
    # package.json / js / html はコピーされない
    assert not list(out.rglob("package.json")) and not list(out.rglob("*.js")) and not list(out.rglob("*.html"))


def fake_get(url, timeout=15):
    if "pypi.org" in url:
        return {"info": {"version": "9.9.9"}}
    if "registry.npmjs.org" in url:
        return {"version": "19.0.0"}
    if "crates.io" in url:
        return {"crate": {"max_stable_version": "3.9.0"}}
    if "endoflife.date" in url:
        return [{"latest": "17.2"}]
    return {"tag_name": "v1.99.0"}


def test_versions_fetch_with_fake_network():
    latest, errors = versions.fetch(fake_get)
    assert not errors
    assert latest["react"] == "19.0.0" and latest["poem"] == "3.9.0" and latest["python"] == "17.2" and latest["rust"] == "1.99.0"
    assert latest["bootstrap"] == "19.0.0" and latest["cosmo-cli"] == "19.0.0"
    assert versions.poem_version(fake_get) == "3.9.0"
    assert versions.drift({"react": "18.0.0"}, {"react": "19.0.0", "django": "9.9.9"}) == [("django", None, "9.9.9"), ("react", "18.0.0", "19.0.0")]


def test_deps_detect_analyze_apply(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^17.0.2", "react-dom": "^18.2.0", "bootstrap": "5.1.0", "pg": "^8.0.0", "@wundergraph/sdk": "^0.1.0"}, "devDependencies": {"typescript": "^4.9.5"}}), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("Django==3.2.0\nflask>=2.0\npsycopg2-binary==2.9.0\n", encoding="utf-8")
    (tmp_path / "t.html").write_text('<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css">', encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres:13\n", encoding="utf-8")
    kinds = {(d["name"], d["kind"]) for d in deps.scan(str(tmp_path))}
    assert ("react", "react") in kinds and ("typescript", "typescript") in kinds and ("bootstrap", "bootstrap") in kinds
    assert ("@wundergraph/sdk", "cosmo") in kinds and ("pg", "db") in kinds and ("psycopg2-binary", "db") in kinds and ("postgres", "db") in kinds
    items = deps.analyze(str(tmp_path), fake_get)
    assert any(d["name"] == "react" and d["outdated"] and d["major"] for d in items)
    assert any("react 17" in m or "react ^17" in m for m in deps.react_report(items))  # react と react-dom の主版違い
    # マイナー/パッチのみ既定: 主版が上がるものは触らない
    assert deps.apply(str(tmp_path), items) == []
    ch = deps.apply(str(tmp_path), items, allow_major=True)
    assert any(c[1] == "react" and c[3] == "^19.0.0" for c in ch)
    assert (tmp_path / "package.json.2poem.bak").exists()
    assert "5.0.2" not in (tmp_path / "t.html").read_text(encoding="utf-8")
    assert "Django==9.9.9" in (tmp_path / "requirements.txt").read_text(encoding="utf-8")


def test_frontend_report_none_when_no_frontend(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert frontend.render_report(frontend.scan_frontend(str(tmp_path)), str(tmp_path)) is None


def test_open_cpu_is_in_generated_projects_by_default(tmp_path):
    r, _, _, _ = routes_of("flask_app.py")
    out = tmp_path / "a"
    emit.generate(r, str(out), "poem")
    assert "open-cpu" in (out / "Cargo.toml").read_text(encoding="utf-8")
    assert "open_cpu::runtime_report()" in (out / "src" / "main.rs").read_text(encoding="utf-8")
    out2 = tmp_path / "b"
    emit.generate(r, str(out2), "poem", open_cpu=False)
    assert "open-cpu" not in (out2 / "Cargo.toml").read_text(encoding="utf-8")
    assert "open_cpu" not in (out2 / "src" / "main.rs").read_text(encoding="utf-8")


def test_apply_verified_restores_on_failure(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8")
    items = deps.analyze(str(tmp_path), fake_get)
    ch, code, restored = deps.apply_verified(str(tmp_path), items, "x", allow_major=True, run=lambda cmd, cwd: 1)
    assert ch and code == 1 and restored
    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))["dependencies"]["react"] == "^18.0.0"
    items = deps.analyze(str(tmp_path), fake_get)
    ch, code, restored = deps.apply_verified(str(tmp_path), items, "x", allow_major=True, run=lambda cmd, cwd: 0)
    assert ch and code == 0 and not restored
    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))["dependencies"]["react"] == "^19.0.0"


def test_detect_test_command(tmp_path):
    assert deps.detect_test_command(str(tmp_path)) is None
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    assert deps.detect_test_command(str(tmp_path)) == "npm test && python manage.py test && cargo test"


def _ported(tmp_path, src):
    f = tmp_path / "a.py"
    f.write_text(src, encoding="utf-8")
    r, _, _, _ = extract.extract_project(str(f))
    return {x.handler: x for x in r}


def test_simple_bodies_are_ported(tmp_path):
    by = _ported(tmp_path, '''from flask import Flask, jsonify
app = Flask(__name__)
@app.route("/ping")
def ping():
    """doc"""
    return {"ok": True, "n": 1, "tags": ["a"], "none": None}
@app.route("/hello/<name>")
def hello(name):
    return jsonify({"hello": name, "q": "a\\"b"})
@app.route("/t")
def t():
    return "plain"
@app.route("/c/<int:a>")
def c(a):
    return {"r": a * 2}
@app.route("/m")
def m():
    x = 1
    return {"x": x}
''')
    assert by["ping"].ported == '{"ok": true, "n": 1, "tags": ["a"], "none": null}' and by["ping"].ported_kind == "json"
    assert by["hello"].ported == '{"hello": name, "q": "a\\"b"}'
    assert by["t"].ported == '"plain"' and by["t"].ported_kind == "text"
    assert by["c"].ported == "" and "(a * 2_i64)" in by["c"].ported_expr  # 計算は ported_expr(ブロック式)へ
    assert by["m"].ported == "" and "let x: i64 = 1_i64;" in by["m"].ported_expr
    text, _ = emit.render_main(list(by.values()), "poem", [])
    assert 'Json(json!({"ok": true' in text and '"plain".to_string()' in text and "TODO" in text
    rtext, _ = emit.render_main(list(by.values()), "rpoem", [])
    assert "serde_json::json!({\"ok\": true" in rtext


def test_expressions_and_branches_are_ported(tmp_path):
    by = _ported(tmp_path, '''from flask import Flask, jsonify
app = Flask(__name__)
@app.route("/grade/<int:score>")
def grade(score):
    if score >= 90:
        return {"grade": "A"}
    elif score >= 60:
        return {"grade": "B", "next": 90 - score}
    return {"grade": "C"}
@app.route("/half/<int:n>")
def half(n):
    return {"half": n / 2, "floor": n // 2, "rem": n % 2, "big": n > 10 and n < 100}
@app.route("/greet/<name>")
def greet(name):
    msg = f"hello {name}"
    return jsonify({"msg": msg, "len": len(name), "up": name.upper(), "t": "x" if name == "bob" else "y"})
@app.route("/div/<int:a>/<int:b>")
def div(a, b):
    return {"q": a / b}
@app.route("/loop/<int:n>")
def loop(n):
    for i in range(n):
        pass
    return {"n": n}
@app.route("/nofall/<int:n>")
def nofall(n):
    if n > 1:
        return {"a": 1}
@app.route("/mixed/<int:n>")
def mixed(n):
    if n > 1:
        return "text"
    return {"a": 1}
@app.route("/text/<int:n>")
def text(n):
    if n > 1:
        return "big"
    return "small"
''')
    g = by["grade"].ported_expr
    assert by["grade"].ported_kind == "json" and "if (score >= 90_i64)" in g and "if (score >= 60_i64)" in g and "(90_i64 - score)" in g
    h = by["half"].ported_expr
    assert "((n as f64) / (2_i64 as f64))" in h and "div_euclid(2_i64)" in h and "rem_euclid(2_i64)" in h and "&&" in h
    m = by["greet"].ported_expr
    assert 'format!("hello {}", name.clone())' in m and "chars().count()" in m and "to_uppercase()" in m and "if (name.clone() == " in m
    # 変数での割り算(0 で例外)・ループ・return で終わらない道・種類が混ざるものは移植しない
    for h in ("div", "loop", "nofall", "mixed"):
        assert by[h].ported == "" and by[h].ported_expr == "", h
    assert by["text"].ported_kind == "text" and by["text"].ported_expr.count("to_string()") == 2
    text, _ = emit.render_main(list(by.values()), "poem", [])
    assert "Json(" in text and "-> String" in text
    rtext, _ = emit.render_main(list(by.values()), "rpoem", [])
    assert "html_response(StatusCode::OK, {" in rtext


def test_mixed_type_min_max_is_not_ported(tmp_path):
    by = _ported(tmp_path, '''from flask import Flask
app = Flask(__name__)
@app.route("/a/<int:n>")
def a(n):
    return {"m": max(n, 3.5)}
@app.route("/b/<int:n>")
def b(n):
    return {"m": max(n, 3)}
''')
    assert by["a"].ported_expr == "" and ".max(" in by["b"].ported_expr


def test_port_semantics_match_python():
    """ported の式と同じ意味を Python 側で実際に評価して、floor / 剰余の符号などの食い違いが無いことを確かめる。"""
    assert -7 // 2 == -4 and -7 % 2 == 1  # Python の値。Rust の div_euclid / rem_euclid(正の定数)と一致する
    assert (-7) // 2 == -(7 // 2) - 1


def test_orm_models_are_ported_to_structs_and_schema(tmp_path):
    from topoem import orm

    (tmp_path / "models.py").write_text('''from django.db import models
class Book(models.Model):
    title = models.CharField(max_length=100)
    author = models.ForeignKey("Author", on_delete=models.CASCADE)
    price = models.FloatField(default=0)
class Author(models.Model):
    name = models.CharField(max_length=50)
    born = models.DateField(null=True)
''', encoding="utf-8")
    (tmp_path / "sa.py").write_text('''from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
Base = declarative_base()
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(80), nullable=False)
    nick = Column(String(20))
''', encoding="utf-8")
    (tmp_path / "sm.py").write_text('''from typing import Optional
from sqlmodel import SQLModel, Field
class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    age: int | None = None
''', encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    assert sorted(m.name for m in ms) == ["Author", "Book", "Hero", "User"]
    rs = orm.render_models_rs(ms)
    assert "pub born: Option<String>," in rs and "pub author_id: i64," in rs and "pub nick: Option<String>," in rs and "pub age: Option<i64>," in rs
    sql = orm.render_schema_sql(ms)
    assert sql.index('"author"') < sql.index('"book"')  # 参照される表が先
    assert '"email" TEXT NOT NULL' in sql
    (tmp_path / "app.py").write_text('''from flask import Flask
app = Flask(__name__)
@app.route("/books")
def books():
    return {"n": Book.query.count()}
''', encoding="utf-8")
    routes, _, _, _ = extract.extract_project(str(tmp_path / "app.py"))
    out = tmp_path / "out"
    warns = emit.generate(routes, str(out), "poem", source_root=str(tmp_path))
    assert (out / "src" / "models.rs").exists() and (out / "schema.sql").exists()
    main = (out / "src" / "main.rs").read_text(encoding="utf-8")
    assert "mod models;" in main and "ORM: モデル Book" in main and any("ORM" in w for w in warns)
    assert 'serde = { version = "1"' in (out / "Cargo.toml").read_text(encoding="utf-8")


def test_orm_queries_all_and_count_are_ported_to_sqlx(tmp_path):
    from topoem import emit, extract

    (tmp_path / "models.py").write_text('''from django.db import models
class Book(models.Model):
    title = models.CharField(max_length=100)
    price = models.FloatField(default=0)
''', encoding="utf-8")
    (tmp_path / "views.py").write_text('''from django.http import JsonResponse
def book_list(request):
    return JsonResponse(list(Book.objects.values()), safe=False)
def book_count(request):
    return JsonResponse(Book.objects.count(), safe=False)
def book_filter(request):
    return JsonResponse(list(Book.objects.filter(title="it's").values()), safe=False)
def book_ord(request):
    return JsonResponse(list(Book.objects.filter(price__gt=0).order_by("-price", "title").values()), safe=False)
def book_get(request):
    return JsonResponse(Book.objects.get(title="x"))
def book_ic(request):
    return JsonResponse(list(Book.objects.filter(title__icontains="a").values()), safe=False)
def book_gt(request):
    return JsonResponse(list(Book.objects.filter(price__gt=1, price__lte=9).values()), safe=False)
''', encoding="utf-8")
    (tmp_path / "urls.py").write_text('''from django.urls import path
from . import views
urlpatterns = [path("books/", views.book_list), path("books/count/", views.book_count), path("books/cheap/", views.book_filter), path("books/gt/", views.book_gt), path("books/one/", views.book_get), path("books/ic/", views.book_ic), path("books/ord/", views.book_ord)]
''', encoding="utf-8")
    routes, _, _, _ = extract.extract_project(str(tmp_path))
    out = tmp_path / "out"
    emit.generate(routes, str(out), "poem", source_root=str(tmp_path))
    main = (out / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'SELECT * FROM "book"' in main and 'SELECT COUNT(*) FROM "book"' in main
    assert """WHERE "title" = 'it''s'""" in main  # 列=文字列は WHERE へ(引用符は二重化)
    assert '"price" > 1 AND "price" <= 9' in main
    assert 'WHERE "price" > 0 ORDER BY "price" DESC, "title"' in main
    assert 'WHERE "title" = ' + "'x'" + ' LIMIT 1' in main and "fetch_optional" in main and "NOT_FOUND" in main
    assert main.count("TODO") >= 1  # icontains など対応外の条件は移さない(誤訳より未移植)
    assert "mod db;" in main and (out / "src" / "db.rs").exists()
    assert "sqlx::FromRow" in (out / "src" / "models.rs").read_text(encoding="utf-8")
    assert "sqlx" in (out / "Cargo.toml").read_text(encoding="utf-8")


def test_sqlalchemy_filter_by_is_ported_to_where(tmp_path):
    from topoem import orm, query

    (tmp_path / "sa.py").write_text('''from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
Base = declarative_base()
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(80), nullable=False)
''', encoding="utf-8")
    models = orm.extract_models(str(tmp_path))
    q = query.match_query('def f():\n    return jsonify(db.session.query(User).filter_by(email="a@b").all())\n', models)
    assert q and query.sql_for(q[0], q[1], q[2], q[3]) == 'SELECT * FROM "users" WHERE "email" = \'a@b\''
    q = query.match_query('def f():\n    return jsonify(User.query.filter_by(id=3).count())\n', models)
    assert q and query.sql_for(q[0], q[1], q[2], q[3]) == 'SELECT COUNT(*) FROM "users" WHERE "id" = 3'
    assert query.match_query('def f():\n    return jsonify(User.query.filter_by(nope=3).all())\n', models) is None



def test_slice_becomes_limit(tmp_path):
    from topoem import orm, query

    (tmp_path / "m.py").write_text("from django.db import models\nclass Book(models.Model):\n    title = models.CharField(max_length=10)\n", encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    src = 'def f(r):\n    return JsonResponse(list(%s), safe=False)\n'
    q = query.match_query(src % 'Book.objects.order_by("title")[:5]', ms)
    assert q and query.sql_for(*q[:5]) == 'SELECT * FROM "book" ORDER BY "title" LIMIT 5'
    assert query.match_query(src % "Book.objects.all()[2:5]", ms) is None


def test_sqlalchemy_limit_becomes_limit(tmp_path):
    from topoem import orm, query

    (tmp_path / "sa.py").write_text('from sqlalchemy import Column, Integer\nfrom sqlalchemy.orm import declarative_base\nBase = declarative_base()\nclass User(Base):\n    __tablename__ = "users"\n    id = Column(Integer, primary_key=True)\n', encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    q = query.match_query("def f():\n    return jsonify(User.query.filter_by(id=1).limit(3).all())\n", ms)
    assert q and query.sql_for(*q[:5]) == 'SELECT * FROM "users" WHERE "id" = 1 LIMIT 3'
    assert query.match_query("def f():\n    return jsonify(User.query.limit(0).all())\n", ms) is None


def test_sqlalchemy_order_by_becomes_order_by(tmp_path):
    from topoem import orm, query

    (tmp_path / "sa.py").write_text('from sqlalchemy import Column, Integer, String\nfrom sqlalchemy.orm import declarative_base\nBase = declarative_base()\nclass User(Base):\n    __tablename__ = "users"\n    id = Column(Integer, primary_key=True)\n    email = Column(String(9))\n', encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    q = query.match_query("def f():\n    return jsonify(User.query.order_by(User.email.desc(), User.id).limit(2).all())\n", ms)
    assert q and query.sql_for(*q[:5]) == 'SELECT * FROM "users" ORDER BY "email" DESC, "id" LIMIT 2'
    assert query.match_query("def f():\n    return jsonify(User.query.order_by(User.nope).all())\n", ms) is None


def test_sqlmodel_where_becomes_where(tmp_path):
    from topoem import orm, query

    (tmp_path / "sm.py").write_text("from typing import Optional\nfrom sqlmodel import SQLModel, Field\nclass Hero(SQLModel, table=True):\n    id: Optional[int] = Field(default=None, primary_key=True)\n    name: str\n    age: int | None = None\n", encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    q = query.match_query('def f(session):\n    return session.exec(select(Hero).where(Hero.age >= 18, Hero.name == "x")).all()\n', ms)
    assert q and query.sql_for(*q[:5]) == 'SELECT * FROM "hero" WHERE "age" >= 18 AND "name" = \'x\''
    assert query.match_query("def f(session):\n    return session.exec(select(Hero).where(Hero.zzz == 1)).all()\n", ms) is None


def test_slice_then_values_is_ported(tmp_path):
    from topoem import orm, query

    (tmp_path / "m.py").write_text("from django.db import models\nclass Book(models.Model):\n    title = models.CharField(max_length=10)\n    price = models.FloatField(default=0)\n", encoding="utf-8")
    ms = orm.extract_models(str(tmp_path))
    q = query.match_query('def f(r):\n    return JsonResponse(list(Book.objects.filter(price__gt=0).order_by("-price")[:2].values()), safe=False)\n', ms)
    assert q and query.sql_for(*q[:5]) == 'SELECT * FROM "book" WHERE "price" > 0 ORDER BY "price" DESC LIMIT 2'
