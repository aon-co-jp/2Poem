# POLICY(2Poem の方針 / Policy)

## 日本語
- **誤訳より未移植**: 型を追えない・Python と Rust で結果が変わりうる・実行時エラーになりうる本体は自動移植せず `TODO` のまま残す。
- **実行はしない**: 変換は `ast` による静的解析のみ。元のコードは実行しない。
- **新しい REST は作らない**(Connect のみ)。JSON の扱いは RS-JSON の方針に従う。C 製の SQLite には依存しない(ORM のスキーマは PostgreSQL 向け)。
- 未了は確認せず自動で進める。実機・実サーバーで確認してから「完了」と書く。秘密(トークン等)は書かない。PyPI は「信頼された発行者」方式でトークンを使わない。
- 現状・完成予定・課題は `README.md` / `CLAUDE.md` / `PORTING.md` の「現状・完成予定・課題」を正とする。

## English
- **Unported beats mistranslated**: bodies whose types cannot be tracked, whose results can differ between Python and Rust, or that could fail at runtime are left as `TODO`.
- **Never execute**: conversion is static analysis through `ast`; the source code is never run.
- **No new REST** (Connect only). JSON handling follows the RS-JSON policy. No dependency on C-based SQLite (ORM schemas target PostgreSQL).
- Unfinished work continues without asking; "done" is written only after checking on real devices/servers; secrets (tokens etc.) are never written. PyPI uses trusted publishing, so no token is used.
- Status, plan and issues live in the "Status, plan and issues" sections of `README.md` / `CLAUDE.md` / `PORTING.md`.
