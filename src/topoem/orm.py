"""ORM のモデル(Django / SQLAlchemy / SQLModel)を取り出し、Rust の構造体と SQL のスキーマにする(実行はしない。ast だけ)。

対象:
  - Django:     `class X(models.Model)` の `name = models.CharField(...)` など
  - SQLAlchemy: `class X(Base)` の `__tablename__` と `Column(...)` / `mapped_column(...)`(`Mapped[...]` 付きも)
  - SQLModel:   `class X(SQLModel, table=True)` の型注釈

出力:
  - `src/models.rs`: `#[derive(Serialize, Deserialize)]` の構造体(日時は ISO 文字列の String)
  - `schema.sql`:    `CREATE TABLE IF NOT EXISTS ...`(PostgreSQL 向けの素直な型だけ。自動採番の主キーは BIGSERIAL なので MariaDB では AUTO_INCREMENT に直す。C 製の SQLite には依存しない)
問い合わせ(`.objects.filter(...)` / `session.query(...)` など)は自動では移植しない。ハンドラの TODO に、使うモデルとテーブル名を添える。
"""
import ast
import os
import re
from dataclasses import dataclass, field

DJANGO_TYPES = {
    "CharField": ("String", "TEXT"), "TextField": ("String", "TEXT"), "SlugField": ("String", "TEXT"), "EmailField": ("String", "TEXT"),
    "URLField": ("String", "TEXT"), "UUIDField": ("String", "TEXT"), "DateField": ("String", "TEXT"), "DateTimeField": ("String", "TEXT"),
    "TimeField": ("String", "TEXT"), "JSONField": ("String", "TEXT"), "DecimalField": ("f64", "DOUBLE PRECISION"),
    "IntegerField": ("i64", "BIGINT"), "BigIntegerField": ("i64", "BIGINT"), "SmallIntegerField": ("i64", "BIGINT"),
    "PositiveIntegerField": ("i64", "BIGINT"), "PositiveSmallIntegerField": ("i64", "BIGINT"),
    "AutoField": ("i64", "BIGINT"), "BigAutoField": ("i64", "BIGINT"), "FloatField": ("f64", "DOUBLE PRECISION"), "BooleanField": ("bool", "BOOLEAN"),
}
SA_TYPES = {
    "Integer": ("i64", "BIGINT"), "BigInteger": ("i64", "BIGINT"), "SmallInteger": ("i64", "BIGINT"), "String": ("String", "TEXT"),
    "Text": ("String", "TEXT"), "Unicode": ("String", "TEXT"), "Float": ("f64", "DOUBLE PRECISION"), "Numeric": ("f64", "DOUBLE PRECISION"),
    "Boolean": ("bool", "BOOLEAN"), "DateTime": ("String", "TEXT"), "Date": ("String", "TEXT"), "Time": ("String", "TEXT"), "JSON": ("String", "TEXT"),
}
PY_TYPES = {"int": ("i64", "BIGINT"), "str": ("String", "TEXT"), "float": ("f64", "DOUBLE PRECISION"), "bool": ("bool", "BOOLEAN"),
            "datetime": ("String", "TEXT"), "date": ("String", "TEXT")}


@dataclass
class Field:
    name: str
    rust: str
    sql: str
    nullable: bool = False
    pk: bool = False
    ref: str = ""  # 外部キーの参照先テーブル


@dataclass
class Model:
    name: str
    table: str
    fields: list = field(default_factory=list)
    kind: str = ""  # django | sqlalchemy | sqlmodel
    file: str = ""


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _true(n):
    return isinstance(n, ast.Constant) and n.value is True


def _false(n):
    return isinstance(n, ast.Constant) and n.value is False


def _call_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


def _ann_type(a):
    """型注釈 → (Rust 型, SQL 型, null 可か)。"""
    if a is None:
        return None
    s = ast.unparse(a).replace("typing.", "")
    if s.startswith("Mapped[") and s.endswith("]"):
        s = s[len("Mapped[") : -1]
    nullable = False
    m = re.match(r"^(?:Optional\[(.+)\]|(.+) \| None|None \| (.+))$", s)
    if m:
        s, nullable = next(g for g in m.groups() if g), True
    base = s.split(".")[-1]
    t = PY_TYPES.get(base)
    return (t[0], t[1], nullable) if t else None


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _django_fields(cls):
    fields, has_pk = [], False
    for st in cls.body:
        if not (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Call)):
            continue
        fname, call = st.targets[0].id, st.value
        kind = _call_name(call)
        nullable = _true(_kw(call, "null") or ast.Constant(False))
        pk = _true(_kw(call, "primary_key") or ast.Constant(False))
        if kind in ("ForeignKey", "OneToOneField"):
            ref = ""
            if call.args and isinstance(call.args[0], (ast.Name, ast.Constant)):
                ref = _snake(call.args[0].id if isinstance(call.args[0], ast.Name) else str(call.args[0].value).split(".")[-1])
            fields.append(Field(fname + "_id", "i64", "BIGINT", nullable, pk, ref))
        elif kind in DJANGO_TYPES:
            r, q = DJANGO_TYPES[kind]
            fields.append(Field(fname, r, q, nullable, pk or kind in ("AutoField", "BigAutoField")))
        else:
            continue
        has_pk = has_pk or fields[-1].pk
    if not has_pk:
        fields.insert(0, Field("id", "i64", "BIGSERIAL", False, True))  # Django は主キーを自動で足す
    return fields


def _sa_fields(cls):
    fields = []
    for st in cls.body:
        call, fname, ann = None, None, None
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Call):
            fname, call = st.targets[0].id, st.value
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and isinstance(st.value, ast.Call):
            fname, call, ann = st.target.id, st.value, st.annotation
        if call is None or _call_name(call) not in ("Column", "mapped_column"):
            continue
        t = None
        for a in call.args:
            nm = _call_name(a) if isinstance(a, ast.Call) else (a.id if isinstance(a, ast.Name) else (a.attr if isinstance(a, ast.Attribute) else ""))
            if nm in SA_TYPES:
                t = SA_TYPES[nm]
                break
            if nm == "ForeignKey":
                t = ("i64", "BIGINT")
        a_t = _ann_type(ann)
        if t is None and a_t:
            t = a_t[:2]
        if t is None:
            continue
        pk = _true(_kw(call, "primary_key") or ast.Constant(False))
        nk = _kw(call, "nullable")
        if pk:
            nullable = False
        elif nk is not None:
            nullable = not _false(nk)
        elif a_t:
            nullable = a_t[2]
        else:
            nullable = True
        ref = ""
        for a in call.args:
            if isinstance(a, ast.Call) and _call_name(a) == "ForeignKey" and a.args and isinstance(a.args[0], ast.Constant):
                ref = str(a.args[0].value).split(".")[0]
        fields.append(Field(fname, t[0], t[1], nullable, pk, ref))
    return fields


def _sqlmodel_fields(cls):
    fields = []
    for st in cls.body:
        if not (isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)):
            continue
        t = _ann_type(st.annotation)
        if not t:
            continue
        pk = isinstance(st.value, ast.Call) and _true(_kw(st.value, "primary_key") or ast.Constant(False))
        fields.append(Field(st.target.id, t[0], t[1], t[2] and not pk, pk))
    return fields


def _models_in(tree, file):
    out = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        bases = [ast.unparse(b) for b in cls.bases]
        if any(b.endswith("models.Model") or b == "Model" for b in bases) and any("models" in b or b == "Model" for b in bases):
            fs = _django_fields(cls)
            out.append(Model(cls.name, _snake(cls.name), fs, "django", file))
        elif any(k.arg == "table" and _true(k.value) for k in cls.keywords) and any(b.endswith("SQLModel") for b in bases):
            out.append(Model(cls.name, _snake(cls.name), _sqlmodel_fields(cls), "sqlmodel", file))
        elif any(isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets) for s in cls.body):
            table = next(s.value.value for s in cls.body if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets) and isinstance(s.value, ast.Constant))
            out.append(Model(cls.name, str(table), _sa_fields(cls), "sqlalchemy", file))
    return [m for m in out if m.fields]


def extract_models(root):
    files = []
    if os.path.isfile(root):
        files = [root]
    else:
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x not in ("venv", ".venv", "node_modules", "__pycache__", ".git", "site-packages", "migrations")]
            files += [os.path.join(d, f) for f in fs if f.endswith(".py")]
    models = []
    for f in sorted(files):
        try:
            with open(f, "rb") as fh:
                src = fh.read().decode("utf-8", "replace")
            tree = ast.parse(src)
        except (SyntaxError, ValueError, OSError):
            continue
        models += _models_in(tree, f)
    return models


def _rs_field(f):
    from .emit import rust_ident

    ident = rust_ident(f.name)
    rename = '    #[serde(rename = "%s")]\n    #[cfg_attr(feature = "sqlx", sqlx(rename = "%s"))]\n' % (f.name, f.name) if ident != f.name else ""
    ty = "Option<%s>" % f.rust if f.nullable else f.rust
    return "%s    pub %s: %s," % (rename, ident, ty)


def render_models_rs(models, sqlx=False):
    parts = ["//! 2Poem が ORM のモデルから生成した構造体(日時・UUID・JSON は文字列で持つ)。SQL は schema.sql。\n#![allow(dead_code)]\nuse serde::{Deserialize, Serialize};\n"]
    seen = set()
    for m in models:
        name = re.sub(r"\W", "_", m.name)
        if name in seen:
            continue
        seen.add(name)
        derive = "#[derive(Debug, Clone, Serialize, Deserialize)]\n" + ('#[derive(sqlx::FromRow)]\n' if sqlx else "")
        fields = "\n".join(_rs_field(f) for f in m.fields)
        if sqlx:
            fields = fields.replace('#[cfg_attr(feature = "sqlx", sqlx(rename = "', '#[sqlx(rename = "').replace('"))]\n', '")]\n')
        else:
            fields = re.sub(r"    #\[cfg_attr\(feature = \"sqlx\", sqlx\(rename = \"[^\"]*\"\)\)\]\n", "", fields)
        parts.append("/// 元: %s(%s)\n%spub struct %s {\n%s\n}\n" % (os.path.basename(m.file), m.kind, derive, name, fields))
    return "\n".join(parts)


def render_schema_sql(models):
    out = ["-- 2Poem が ORM のモデルから生成したスキーマ(PostgreSQL 向け。MariaDB では BIGSERIAL を AUTO_INCREMENT に直す)。マイグレーションの代わりではなく、写しです。\n"]
    # 参照される表を先に作る(外部キーの参照先が後ろにあっても通るようにする)
    pending, ordered, done = list({m.table: m for m in models}.values()), [], set()
    names = {m.table for m in pending}
    while pending:
        ready = [m for m in pending if all((f.ref not in names) or f.ref in done or f.ref == m.table for f in m.fields)]
        if not ready:
            ready = pending[:1]  # 循環参照は順番どおりに出す(後で ALTER が要る)
        for m in ready:
            ordered.append(m)
            done.add(m.table)
            pending.remove(m)
    for m in ordered:
        cols = []
        for f in m.fields:
            c = '  "%s" %s' % (f.name, f.sql)
            if f.pk:
                c += " PRIMARY KEY"
            elif not f.nullable:
                c += " NOT NULL"
            if f.ref:
                c += ' REFERENCES "%s"' % f.ref
            cols.append(c)
        out.append('CREATE TABLE IF NOT EXISTS "%s" (\n%s\n);\n' % (m.table, ",\n".join(cols)))
    return "\n".join(out)
