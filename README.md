# 2Poem

**Flask / Django / FastAPI** で組まれた既存システムを、**Rust + [Poem](https://github.com/poem-web/poem)** または **[RPoem](https://github.com/aon-co-jp/RPoem)** の骨組みへ自動変換し、Python・Rust・JavaScript・TypeScript・React・Bootstrap・WunderGraph Cosmo・データベースの**新版への追従**を支援するツールです。`2FastAPI`・`MFastAPI`・`RMojo` と同じ流儀で、**起動時に自動バージョンアップ**します。

Converts existing Flask / Django / FastAPI systems into a Rust + Poem or RPoem skeleton and helps you follow new releases of Python, Rust, JavaScript, TypeScript, React, Bootstrap, WunderGraph Cosmo and databases. Like its siblings it **self-updates at startup**.

```bash
pip install topoem            # (リリースの wheel から。PyPI 公開は未了)
2poem scan examples/flask_app.py
2poem convert examples/flask_app.py -o out        # Flask/FastAPI -> Poem
2poem convert examples/django_proj -o out         # Django -> RPoem(自動選択。--target で指定)
2poem deps ./myproject [--apply] [--major]        # JS/TS/React/Bootstrap/Cosmo/DB/Python の依存の検出と版の書き換え
2poem versions --compat compat.json               # 上流の最新版
2poem update                                      # 2Poem 自身を今すぐ更新
```

## 何を変換するか(正直な範囲)
- **骨組み**: ルート・メソッド・パスパラメータの型(`<int:id>`・`{id}`・`(?P<id>\d+)` → `Path<i64>` など)・ハンドラ名を写す。各ハンドラの本体は元の Python の先頭数行をコメントに添えた `TODO`(**業務ロジックは自動では移植しません**)。
- **Python の世代**: 解析は実行中の Python 自身の `ast` を使うので、新しい Python(3.x の先、将来の 4 以降)を入れればその文法にそのまま追従。**Python 2** は `print` 文・`except X, e`・`raise X, msg`・`iteritems()` などをよくある範囲で 3 系へ直してから読む(完全ではない。直せなかった点は警告)。
- **ターゲットの自動選択**: Django(管理・ORM・認証を含む重い構成)→ **RPoem**、Flask/FastAPI → **Poem**。`--target poem|rpoem` で上書き。
- **RPoem への変換は、Node.js・JavaScript・TypeScript・React・Bootstrap・WunderGraph Cosmo・手書きの HTML5/CSS3 を基本的に使わない**: 画面を返すハンドラは `open-runo-view`(Rust の VNode を SSR)で返し、`package.json`・`.js/.ts/.tsx`・`.css`・テンプレート HTML・Cosmo の設定は**コピーせず** `MIGRATION.md` に「何があって、RPoem の何へ置き換えるか」を書き出す。ブラウザへ届く HTML は Rust が生成する(ブラウザ自体は HTML を要るため、ゼロにはできない)。
- RPoem の互換面は PATCH を持たないため PUT として登録し警告する。

## 新版への追従
- `2poem versions`: Python・Rust・Node.js・TypeScript・React・Bootstrap・PostgreSQL・MySQL・SQLite・Django・Flask・FastAPI・Starlette・Poem・RPoem・WunderGraph Cosmo(`wgc`)の最新版。生成する `Cargo.toml` の `poem` は常に crates.io の最新の安定版(取得できなければ固定値)。
- `2poem deps`: `package.json`・`requirements*.txt`・`Cargo.toml`・Django の `DATABASES`・docker-compose の DB イメージ・テンプレートの CDN 参照から、**JavaScript / TypeScript / React / Bootstrap / WunderGraph Cosmo / DB / Python フレームワーク**の依存を検出し、古いものを示す。`--apply` で版を書き換え(元は `.2poem.bak`、既定はマイナー/パッチのみ、`--major` で解禁、lock は触らない)。**React は 15〜19 以降の全世代**を対象に、react と react-dom・@types/react の主版の不一致なども点検する。
- CI を毎日実行(最新の Python と、最新の poem での変換結果のコンパイル)。失敗したら issue を起こす。

## 起動時の自動バージョンアップ
起動のたびに、前回の確認から24時間以上たっていれば切り離した子プロセスで最新リリースを調べ、新しければ `pip download --no-index`(PyPI は見ない)→ **リリースの `.sha256` と一致したときだけ** `pip install`。起動は遅くならない。止めるには `TOPOEM_NO_UPDATE=1`(`CI` 設定時も停止)。状態は `~/.2poem/`。信頼の根拠は GitHub への TLS と SHA-256(電子署名ではない)。

## 未了
PyPI 公開(所有者が pypi.org で「信頼された発行者」を登録し、リポジトリ変数 `PYPI_PUBLISH=true` を立てると、タグのたびに自動公開。ワークフローは用意済み)、ORM の問い合わせ(`.objects.filter` / `session.query`)・テンプレートの変換、ループ・関数呼び出しを含む業務ロジックの移植、macOS/Linux 実機でのインストール試験。

MIT License.

## v0.1.1 の追加 / Added in v0.1.1
- 生成する Rust プロジェクトに open-cpu を既定で同梱(`--no-open-cpu` で外せる)。aruaru-llm は、VPS での推論が遅すぎる(GPT-2 で60秒超)ため**同梱していません**(共有基盤方針: VPS に1回だけ入れて呼ぶ)。
- `2poem deps --apply --verify "<試験コマンド>"`: 版を書き換えた後に試験を実行し、失敗したら元のファイルへ自動で戻す。
- Generated Rust projects bundle open-cpu by default (`--no-open-cpu` to opt out); aruaru-llm is intentionally not bundled (too slow on the VPS). `deps --verify` restores the originals if tests fail.

## 現状・完成予定・課題 / Status, plan and issues (2026-11-22, v0.1.4)
**現状(日本語)**
- Flask / Django / FastAPI を Rust(Poem / RPoem)の骨組みへ変換。ルーティング・パスパラメータの型・メソッドを写す。
- 業務ロジックの自動移植: 定数・パラメータ・dict/list に加え、**計算式・比較・三項・f文字列・代入・`if/elif/else`**(全部の道が return で終わるもの)を型を追って Rust にする。生成サーバーを実際に起動し Python と同値を確認。
- ORM: Django / SQLAlchemy / SQLModel のモデルを `src/models.rs`(serde 構造体)と `schema.sql`(PostgreSQL 向け)へ。
- 生成物に open-cpu を既定で同梱。`deps --apply --verify auto` で依存更新(失敗時は自動復元)。試験26件、CI(3 OS × 3 Python、生成物の cargo check)全成功。
- ORM の問い合わせ(未リリース、`main`。WSL の PostgreSQL 16 で生成アプリを実際に起動し、全件取得・件数・`filter`・文字列の引用符・`get`・`order_by`+`[:N]`・404 の応答が、Poem 版・RPoem 版の両方で正しいことを確認済み): 本体が単一の `return` で、`M.objects.all()` / `list(M.objects.values())` / `.count()`、`session.query(M).all()/.count()`、`M.query.all()/.count()`、`session.exec(select(M)).all()` のどれかなら、sqlx(PostgreSQL、純 Rust)の問い合わせへ移す(`src/db.rs`、接続先は環境変数 `DATABASE_URL`)。`filter`・`get`・作成・更新・削除などは移さず TODO のまま。Poem 版・RPoem 版とも `cargo build` 成功。**実際の DB への接続は未確認**(この PC に PostgreSQL が無い)。 さらに Django の `filter(列=整数・真偽・文字列)` は、列がモデルにあるときだけ `WHERE` へ移す(`__gt`・`__gte`・`__lt`・`__lte` も移す。`icontains` などは移さない。SQLAlchemy / Flask-SQLAlchemy の `filter_by(列=リテラル)` も同じ `WHERE` へ移す。`get(列=リテラル)`(`pk=` も可)は `LIMIT 1` の1件取得に移し、なければ 404 を返す。SQLModel の `session.exec(select(M).where(M.列 == / > / >= / < / <= リテラル, ...)).all()` も `WHERE` へ、SQLAlchemy / Flask-SQLAlchemy の `order_by(M.列 / M.列.desc() / M.列.asc())` も `ORDER BY` へ、`limit(N)` も `LIMIT N` へ、`order_by(...)[:N].values()` のように切り出しの後ろに `.values()` が続く形も移す。`[:N]`(先頭からの切り出し、1〜10000)は `LIMIT N` へ、`order_by("列", "-列")` は列が実在するときだけ `ORDER BY` へ移す(件数では無視)。文字列は引用符を二重化し、`"` と `\` を含むものは移さない)。

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
- ORM queries (unreleased, on `main`; the generated app was run against PostgreSQL 16 in WSL and the list, count, `filter`, quoted-string, `get`, `order_by` + `[:N]` and 404 responses were verified for both the Poem and RPoem outputs): when the body is a single `return` of `M.objects.all()` / `list(M.objects.values())` / `.count()`, `session.query(M).all()/.count()`, `M.query.all()/.count()` or `session.exec(select(M)).all()`, it becomes an sqlx (PostgreSQL, pure Rust) query (`src/db.rs`, connection string from env `DATABASE_URL`). `filter`, `get`, create, update and delete stay `TODO`. Both the Poem and RPoem outputs pass `cargo build`. **A real database connection is unverified** (no PostgreSQL on this PC). Django `filter(column=int/bool/string)` is also ported to a `WHERE` clause, only when the column exists on the model (`__gt`, `__gte`, `__lt` and `__lte` are ported too; lookups such as `icontains` are not; SQLAlchemy / Flask-SQLAlchemy `filter_by(column=literal)` is ported to the same `WHERE`; `get(column=literal)` (`pk=` too) becomes a `LIMIT 1` fetch that returns 404 when nothing matches; SQLModel `session.exec(select(M).where(M.col == / > / >= / < / <= literal, ...)).all()` also becomes a `WHERE`; SQLAlchemy / Flask-SQLAlchemy `order_by(M.col / M.col.desc() / M.col.asc())` also becomes `ORDER BY`, and `limit(N)` also becomes `LIMIT N`; a `.values()` after the slice (`order_by(...)[:N].values()`) is ported too; `[:N]` (a leading slice, 1 to 10000) becomes `LIMIT N`; `order_by("col", "-col")` becomes `ORDER BY` only when the columns exist (ignored for counts); strings get doubled quotes, and any containing `"` or a backslash are skipped).

**Planned**: porting ORM queries such as `filter` and running them against PostgreSQL, templates, bodies with loops/function calls, and the PyPI release.
**Issues**
1. PyPI: the owner must register a pending publisher on pypi.org (project `topoem` / owner `aon-co-jp` / repo `2Poem` / workflow `ci.yml` / environment `pypi`), set the repository variable `PYPI_PUBLISH=true` and push the next tag (workflow is ready, no token needed).
2. Because unported is preferred over mistranslated, division by a variable, mixed-type `max(7, 3.5)`, chained comparisons, loops and bare string returns stay `TODO`. Integers are i64 (not Python bigints).
3. Real devices: Mac / iPhone / real BSD are unverified (FreeBSD 14.5 was checked with RMojo in QEMU on Windows). aruaru-llm is not bundled because it is slow on the VPS.
