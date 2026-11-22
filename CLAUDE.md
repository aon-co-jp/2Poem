# 2Poem 開発メモ

- 目的: Flask/Django/FastAPI → Rust + Poem / RPoem の骨組みへの自動変換と、上流(Python・Rust・JS・TS・React・Bootstrap・Cosmo・DB)の新版追従。`F:\2Poem`(新規 2026-11-17)。標準ライブラリのみ。
- 正本の方針は [`runo/CLAUDE.md`](https://github.com/aon-co-jp/runo)。RPoem 変換は Node.js/JS/TS/React/Bootstrap/Cosmo/手書き HTML・CSS を基本的に使わない(ユーザー指示 2026-11-17)。`frontend.py` が資産を `MIGRATION.md` に記録、`emit.py` が `open-runo-view` の SSR を返す。
- 試験: `python -m pytest -q`。生成物のコンパイル確認は `2poem convert examples/... -o OUT` → `cargo check`(RPoem は `--rpoem-path`)。
- 自動更新: `_autoupdate.py`(2FastAPI/MFastAPI と同じ流儀。`TOPOEM_NO_UPDATE=1` で停止)。リリースに `.sha256` が要る(CI が付ける)。
- 注意: PowerShell 5.1 で日本語を含むファイルを書かない。
- 2026-11-17 v0.1.1: 生成物に open-cpu 同梱、`deps --verify`。aruaru-llm は意図的に非同梱。未了と再開用メッセージ(日英)は `PORTING.md` 末尾。/ v0.1.1: open-cpu bundled in generated projects, `deps --verify`; aruaru-llm intentionally not bundled. Open items and the resume message (JA/EN) are at the end of PORTING.md.

## 現状・完成予定・課題 / Status, plan and issues (2026-11-22, v0.1.4)
**現状(日本語)**
- Flask / Django / FastAPI を Rust(Poem / RPoem)の骨組みへ変換。ルーティング・パスパラメータの型・メソッドを写す。
- 業務ロジックの自動移植: 定数・パラメータ・dict/list に加え、**計算式・比較・三項・f文字列・代入・`if/elif/else`**(全部の道が return で終わるもの)を型を追って Rust にする。生成サーバーを実際に起動し Python と同値を確認。
- ORM: Django / SQLAlchemy / SQLModel のモデルを `src/models.rs`(serde 構造体)と `schema.sql`(PostgreSQL 向け)へ。
- 生成物に open-cpu を既定で同梱。`deps --apply --verify auto` で依存更新(失敗時は自動復元)。試験26件、CI(3 OS × 3 Python、生成物の cargo check)全成功。
- ORM の問い合わせ(未リリース、`main`): 本体が単一の `return` で、`M.objects.all()` / `list(M.objects.values())` / `.count()`、`session.query(M).all()/.count()`、`M.query.all()/.count()`、`session.exec(select(M)).all()` のどれかなら、sqlx(PostgreSQL、純 Rust)の問い合わせへ移す(`src/db.rs`、接続先は環境変数 `DATABASE_URL`)。`filter`・`get`・作成・更新・削除などは移さず TODO のまま。Poem 版・RPoem 版とも `cargo build` 成功。**実際の DB への接続は未確認**(この PC に PostgreSQL が無い)。 さらに Django の `filter(列=整数・真偽・文字列)` は、列がモデルにあるときだけ `WHERE` へ移す(`__gt`・`__gte`・`__lt`・`__lte` も移す。`icontains` などは移さない。SQLAlchemy / Flask-SQLAlchemy の `filter_by(列=リテラル)` も同じ `WHERE` へ移す。`get(列=リテラル)`(`pk=` も可)は `LIMIT 1` の1件取得に移し、なければ 404 を返す。`order_by("列", "-列")` は列が実在するときだけ `ORDER BY` へ移す(件数では無視)。文字列は引用符を二重化し、`"` と `\` を含むものは移さない)。

**完成予定**: ORM の `filter` などの問い合わせの移植と PostgreSQL での実行確認、テンプレートの変換、ループ・関数呼び出しを含む本体の移植、PyPI 公開。
**課題**
1. PyPI: 所有者が pypi.org で pending publisher(project `topoem` / owner `aon-co-jp` / repo `2Poem` / workflow `ci.yml` / environment `pypi`)を登録し、リポジトリ変数 `PYPI_PUBLISH=true` を設定して次のタグを push する必要がある(ワークフローは用意済み、トークン不要)。
2. 誤訳より未移植を選ぶ方針のため、変数での割り算、型が混ざる `max(7, 3.5)`、連鎖比較、ループ、文字列の直接 return は `TODO` のまま。整数は i64(Python の大きい整数とは違う)。
3. 実機確認: Mac / iPhone / 実機 BSD は未確認(FreeBSD 14.5 は Windows 上の QEMU で RMojo を確認済み)。aruaru-llm は VPS で遅いため同梱しない。

**Status (English)**
- Converts Flask / Django / FastAPI into a Rust (Poem / RPoem) skeleton, copying routes, path-parameter types and methods.
- Automatic business-logic porting: besides constants, parameters and dict/list, it now ports **arithmetic, comparisons, ternaries, f-strings, assignments and `if/elif/else`** (when every path returns) with type tracking. A generated server was run and matched Python's values.
- ORM: Django / SQLAlchemy / SQLModel models become `src/models.rs` (serde structs) and `schema.sql` (PostgreSQL-oriented).
- Generated projects bundle open-cpu by default; `deps --apply --verify auto` updates dependencies with automatic restore. 26 tests; CI (3 OS x 3 Python, cargo check of generated output) is all green.
- ORM queries (unreleased, on `main`): when the body is a single `return` of `M.objects.all()` / `list(M.objects.values())` / `.count()`, `session.query(M).all()/.count()`, `M.query.all()/.count()` or `session.exec(select(M)).all()`, it becomes an sqlx (PostgreSQL, pure Rust) query (`src/db.rs`, connection string from env `DATABASE_URL`). `filter`, `get`, create, update and delete stay `TODO`. Both the Poem and RPoem outputs pass `cargo build`. **A real database connection is unverified** (no PostgreSQL on this PC). Django `filter(column=int/bool/string)` is also ported to a `WHERE` clause, only when the column exists on the model (`__gt`, `__gte`, `__lt` and `__lte` are ported too; lookups such as `icontains` are not; SQLAlchemy / Flask-SQLAlchemy `filter_by(column=literal)` is ported to the same `WHERE`; `get(column=literal)` (`pk=` too) becomes a `LIMIT 1` fetch that returns 404 when nothing matches; `order_by("col", "-col")` becomes `ORDER BY` only when the columns exist (ignored for counts); strings get doubled quotes, and any containing `"` or a backslash are skipped).

**Planned**: porting ORM queries such as `filter` and running them against PostgreSQL, templates, bodies with loops/function calls, and the PyPI release.
**Issues**
1. PyPI: the owner must register a pending publisher on pypi.org (project `topoem` / owner `aon-co-jp` / repo `2Poem` / workflow `ci.yml` / environment `pypi`), set the repository variable `PYPI_PUBLISH=true` and push the next tag (workflow is ready, no token needed).
2. Because unported is preferred over mistranslated, division by a variable, mixed-type `max(7, 3.5)`, chained comparisons, loops and bare string returns stay `TODO`. Integers are i64 (not Python bigints).
3. Real devices: Mac / iPhone / real BSD are unverified (FreeBSD 14.5 was checked with RMojo in QEMU on Windows). aruaru-llm is not bundled because it is slow on the VPS.
