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
