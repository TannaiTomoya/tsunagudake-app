# 02｜技術仕様書 — つなぐだけ（Tsunagudake）

版2.1・2026-11-19（依存と公開の構成を、実際に動かして確かめた。§3・§6・§7・§11・§14）。
要件は [01 要件定義書](01-requirements.md)、テーブル・規則・画面・テスト・作る順番は [03 詳細設計書](03-detailed-design.md)、権限は [authorization.md](authorization.md)、公開の手順は [runbook.md](runbook.md)。

---

## 1. この文書の役目

「何で作るか」「どう守るか」「どこに置くか」を決める。**ここに無い部品は、足す前にこの文書を直す。**

手本にしたもの：

| 手本 | 何を借りたか |
|---|---|
| キャンプの教材（Python + Flask + nginx + MySQL、05・07・11・12章） | 開発の型、公開の手順、バックアップ |
| 受講生の制作（Flask の本番向けの構成。以下「手本のリポジトリ」） | 設定の切り替え、起動時の検査、回数制限、デモ環境、文書の粒度（§13） |
| 姉妹アプリ torudake（Laravel） | 設計書3本の形、AGENTS.md、1機能ずつ進める型 |

## 2. 技術の選び方（決めたことと理由）

| 決めたこと | 選ばなかったもの | 理由 |
|---|---|---|
| **Flask 3.1** | Django・Streamlit | キャンプで学んだ型と、公開の手順（05・07・11・12章）がそのまま使える。講師に見てもらえる。Streamlit は「発表用の別物」になり、作り直しが出る。Django は管理画面と認証が付くが、発表の3日前に新しい型を覚える賭けは、機能の賭けと重ねない |
| 規則は **`tsunagu_rules`**（Flask を知らない純粋な関数） | ビューやモデルの中に書く | 同じ入力なら同じ答え、をテストで証明できる。あとで別の画面や道具（F45・F90）からも呼べる |
| 認証は **Flask-Login**、フォームと CSRF は **Flask-WTF** | 自作・Flask-Security | キャンプと手本と同じ。部品を増やさない |
| DB は **Flask-SQLAlchemy ＋ Flask-Migrate**。開発は SQLite、demo・本番は **MySQL** | 本番も SQLite | 複数の人が同時に書く。行のロックが要る（03 §7）。キャンプの手順が MySQL |
| 回数制限は **DB のテーブル**で数える | Flask-Limiter（＋ Redis） | gunicorn の複数プロセスで数を共有するのに、別のサーバー（Redis）を増やさない。手本と同じ形 |
| パスワードは **Werkzeug の `generate_password_hash`**（Flask に付属） | 別のライブラリ | 既定が安全な方式。部品を増やさない |
| 見た目は **Pico CSS 2 ＋ 自分の `theme.css`**。ファイルは自分のサーバーに置く | CDN・Tailwind・JS のフレームワーク | 外の読み込みを0にすると、CSP が1行で済み、会場の回線に左右されない。意味どおりの HTML で、フォーカスやエラーの見た目が最初から付く |
| JavaScript は **P0 では書かない** | htmx・React | 画面はフォームとリンクだけで通る。足すのは、必要が出てから |
| **ファイルのアップロードを作らない** | 合格証の画像を預かる | 置かないものは漏れない。検査・保存・削除の仕組みが丸ごと要らなくなる |
| **決済を作らない** | Stripe | お金を預からない（01 §6.9）。許可・返金・二重払いの設計が丸ごと要らなくなる |
| AI は**既定で使わない**（§9） | 最初から AI で翻訳 | 規則で訳せる。説明できる。鍵も費用も要らない |
| 公開は **VPS（Ubuntu 24.04）＋ nginx ＋ gunicorn ＋ systemd** | 共用のレンタルサーバー・PaaS | キャンプの手順どおり。共用のレンタルサーバーは root 権限がなく、この構成は動かせない（§11） |

## 3. 構成

| 層 | 使うもの | メモ |
|---|---|---|
| 言語 | Python 3.14 | キャンプと同じ。`.python-version` に書き、uv が入れる |
| パッケージ管理 | uv（`pyproject.toml`・`uv.lock`） | `uv.lock` は Git に入れる。本番は `uv sync --frozen --no-dev` |
| Web | Flask 3.1 | アプリケーションファクトリ `app:create_app()` |
| 認証 | Flask-Login | セッションは署名つき Cookie |
| フォーム | Flask-WTF（WTForms）・email-validator | 全フォームに CSRF |
| DB | Flask-SQLAlchemy 3.1（SQLAlchemy 2 の型つきの書き方）・Flask-Migrate（Alembic） | 変更は必ずマイグレーション |
| DB の接続 | PyMySQL・cryptography | MySQL 8 の認証に cryptography が要る |
| 設定 | python-dotenv | `.env` と `.flaskenv` |
| 本番のサーバー | gunicorn（127.0.0.1:8000）← nginx（80・443） | systemd で自動起動 |
| 見た目 | Pico CSS 2（`app/static/vendor/`）＋ `app/static/css/theme.css` | §8 |
| 検査 | Ruff（整形・静的解析）・mypy・pytest | §10 |
| CI・反映 | GitHub Actions（`ci.yml`・`deploy.yml`）＋ `deploy.sh` | §11.4 |

依存は少なく保つ（手本は13個）。`pyproject.toml` の形：

```toml
[project]
name = "tsunagudake"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "cryptography",
    "email-validator",
    "flask",
    "flask-login",
    "flask-migrate",
    "flask-sqlalchemy",
    "flask-wtf",
    "gunicorn",
    "pymysql",
    "python-dotenv",
]

[dependency-groups]
dev = ["mypy", "pytest", "ruff"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

版は `uv add` した時点の最新を入れ、**`uv.lock` を正とする**（設計書に版の数字を書き写さない。食い違いのもとになる）。
2026-11-19 に確かめたこと：この依存の組は、uv 0.11・Python 3.14.6 で入る（Flask 3.1.3・Flask-SQLAlchemy 3.1.1・SQLAlchemy 2.1.9・Werkzeug 3.1.9・gunicorn 27.0.1 に解決された）。拡張を全部載せた最小のアプリで `/healthz` が 200、パスワードのハッシュは scrypt。
段階0で `uv.lock` を作ってコミットし、以後はそれを正とする。

## 4. フォルダ

```
tsunagudake-app/
├─ app/
│  ├─ __init__.py          # create_app()。設定を読み、起動時の検査をし、Blueprint を登録
│  ├─ config.py            # Development / Test / Demo / Production
│  ├─ extensions.py        # db・migrate・login_manager・csrf
│  ├─ models/              # テーブル（03 §4）。1ファイル1かたまり
│  ├─ services/            # 状態を変える処理（公開・声かけ・合意・確認・確定）。トランザクションはここだけ
│  ├─ authz.py             # 権限の判定（事務所から引く・ロールを確かめる）。正本は docs/authorization.md
│  ├─ audit.py             # 監査記録
│  ├─ ratelimit.py         # 回数制限
│  ├─ security.py          # Host の確認・応答ヘッダー（CSP など）
│  ├─ cli.py               # flask create-user / seed-demo / reset-demo / verify-ledger
│  ├─ blueprints/
│  │  ├─ public.py         # / ・ /how ・ /healthz
│  │  ├─ auth.py           # /login ・ /logout ・（demo）/demo/enter
│  │  ├─ office.py         # /o/<office_id>/…
│  │  ├─ tsunagite.py      # /t/…
│  │  ├─ region.py         # /r/<municipality_code>/
│  │  └─ admin.py          # /admin/…
│  ├─ templates/           # Jinja2。base.html と、Blueprint ごとのフォルダ
│  └─ static/
│     ├─ vendor/pico.min.css
│     └─ css/theme.css     # 色・文字・余白は、ここの変数だけで決める
├─ tsunagu_rules/          # 規則（03 §5）。Flask も DB も import しない
│  ├─ translate.py  value.py  match.py  fee.py  ledger.py  region.py
│  ├─ types.py             # 入力と出力の型（dataclass・Enum）
│  └─ dictionary_v1.json   # 翻訳の辞書（版つき）
├─ migrations/             # Flask-Migrate が作る
├─ tests/
│  ├─ rules/               # 規則のテスト（DB なし。速い）
│  └─ app/                 # 画面・権限・状態のテスト
├─ deploy/                 # nginx・systemd・deploy.sh・backup の雛形（秘密は入れない）
├─ docs/                   # 設計書
├─ .claude/agents/         # 点検だけをする担当（§12）
├─ .github/workflows/      # ci.yml・deploy.yml
├─ .env.example  .flaskenv  .gitignore  .python-version
├─ pyproject.toml  uv.lock  Makefile
└─ AGENTS.md  CLAUDE.md  README.md
```

## 5. 設定と環境

### 5.1 4つの動かし方

| `APP_ENV` | 使う場面 | DB | デバッグ | デモの入口（F62） | 足りないとき |
|---|---|---|---|---|---|
| `development` | 手元 | SQLite | あり | 動く | 開発用の固定の鍵で動く |
| `test` | pytest | SQLite（`TEST_DATABASE_URL` があれば MySQL） | なし | 動く | — |
| `demo` | 発表（公開。データは架空） | MySQL | なし | 動く | **起動しない** |
| `production` | 実際の事務所が使う | MySQL | なし | **動かない（404）** | **起動しない** |

**`APP_ENV` が未設定なら `production` として動く。** 設定を忘れたサーバーが、甘い設定で公開されないようにするため。

### 5.2 起動時の検査（demo・production）

`create_app()` の中で確かめ、1つでも外れたら、理由を出して止まる（AC11）。

| 確かめること | 外れたとき |
|---|---|
| `SECRET_KEY` が32文字以上で、雛形の値でない | 止まる |
| `DATABASE_URL` が `mysql+pymysql://` で始まる | 止まる（SQLite で公開しない） |
| `BASE_URL` が `https://` で始まる | 止まる |
| `TRUSTED_HOSTS` が空でない | 止まる |
| `SESSION_COOKIE_SECURE=1` | 止まる |
| demo のとき `DEMO_PASSWORD` が12文字以上 | 止まる |
| production のとき `AI_DRIVER=fake` でない | 止まる |

### 5.3 `.env` と `.gitignore`（必ず守る）

- **`.env` は Git に入れない。人に見せない。チャットや AI に貼らない。**
- 項目の一覧は [`.env.example`](../.env.example)（値は空）。項目を足したら、同じコミットで `.env.example` と §5.2 を直す。
- `.flaskenv` は Git に入れる。書くのは `FLASK_APP=app:create_app` だけ。
- `.gitignore` は、秘密（`.env`・鍵・`.my.cnf`・`s3.env`・`*.htpasswd`）、データ（`instance/`・`*.sqlite`・`*.sql.gz`・`backup/`）、Python の作業ファイルを除く。**`uv.lock` は除かない。**
- コミットの前に `git status` で、入れるファイルを目で確かめる。`git add .` は使わず、ファイルを名前で足す。
- CI で、`.env` や鍵の形をした文字列がコミットに入っていないかを確かめる（§10.3）。
- もし入れてしまったら：その鍵・パスワードを**すぐ作り直す**（履歴から消すより先）。公開リポジトリでは、1度 push したものは読まれたと考える。

## 6. コードの決まり

| 項目 | 決まり |
|---|---|
| 入口 | Blueprint は、受け取る・権限を確かめる・`services` を呼ぶ・表示する、だけ。計算は `tsunagu_rules`、状態の変更は `services` |
| 権限 | 事務所の画面は必ず `/o/<office_id>/…`。子は**事務所から引く**（`Need` を id だけで引かない）。見つからない・他の事務所のものは 404、ロールの不足は 403（[authorization.md](authorization.md)） |
| 状態の変更 | `services` の1つの関数＝1つのトランザクション。行をロックしてから状態を確かめる（03 §7） |
| 一括の更新・削除 | 業務のテーブルに使わない（1件ずつ、状態を確かめて） |
| フォーム | 画面から受け取るのは、WTForms で定義した項目だけ。`office_id`・`role`・`status`・金額の計算結果は、**画面から受け取らない** |
| 金額・割合 | 円は整数。割合は万分率（bp）の整数。小数を使わない。端数は切り捨て |
| 日時 | DB は UTC。画面は日本時間（`zoneinfo`）。日付は「11月19日(木)」、金額は「32,000円」 |
| 状態・種類 | 文字列（VARCHAR）で保存し、取りうる値は Python の Enum で制限（SQLite と MySQL で同じに動かす） |
| 文言 | 画面は日本語・敬体。エラーは、どの欄の何が違うかを書く。英語の既定の文言を残さない |
| ログ | 個人情報・困りごとの本文・パスワード・鍵を、ログに出さない |
| 規則の版 | `tsunagu_rules.RULES_VERSION` を、翻訳・候補・明細・台帳の行に残す |
| DB の接続 | `SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800}`。MySQL は、しばらく使わない接続を切る。入れないと、**朝いちばんの1回がエラーになる** |
| 制約にぶつかったとき | 例外の種類は DB で違う。MySQL では、CHECK 制約は `OperationalError`、長すぎる文字は `DataError`、一意制約は `IntegrityError`（実際に確かめた）。`services` は3つとも受けて巻き戻し、決まった文言を返す（500 にしない） |

## 7. 安全

| 守るもの | やり方 |
|---|---|
| パスワード | ハッシュで保存。12文字以上。変えたら `session_nonce` を新しくし、前のログイン状態をすべて切る |
| セッション | Cookie は Secure・HttpOnly・SameSite=Lax。ログインで作り直す。8時間で切れる |
| CSRF | Flask-WTF の `CSRFProtect` を全体にかける。状態を変える操作は POST だけ |
| Host | `TRUSTED_HOSTS` 以外の Host は 400 |
| 応答ヘッダー | `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、HTTPS では HSTS |
| 回数制限 | ログインの失敗：同じメールアドレスで15分に5回。同じ IP から10分に20回。止めたら 429 と Retry-After を返す。成功したメールアドレスの失敗のみ取り消す。相手は鍵付き SHA-256 で数える（03 §4.8）。nginx は、相手の IP を `X-Forwarded-For` に**上書きして**渡す（相手が付けてきた値を引き継がない） |
| 登録の有無 | ログインの失敗は、登録の有無で文言も時間も変えない |
| 人を作るコマンド | `flask create-user` は、パスワードを引数で受け取らず、対話で聞く（コマンドの履歴に残さない） |
| 権限 | [authorization.md](authorization.md) の拒否のマス1つにつき、テスト1本。守りを外すとテストが落ちることを確かめる |
| 入力 | SQL は ORM だけ（文字列でつながない）。テンプレートは自動エスケープのまま（`|safe` を使わない） |
| 秘密 | §5.3。本番の鍵は本番の `.env` にだけ置く（`chmod 600`）。DB のユーザーはアプリごとに分ける |
| デモの入口 | demo と development でだけ登録される。production では、ルートそのものが無い（AC10） |
| 運営の閲覧 | 運営が事務所の画面を開いたら、監査記録に残る |
| 依存 | `uv.lock` で固定。月1回 `uv lock --upgrade` → 全テスト |

## 8. 見た目と使いやすさ

「AIっぽさ」は、決めていない所を平均で埋めた結果。**先に決めて、引く。**

### 8.1 参考にする2つ（真似できる言葉で）
| 参考 | 良いところ |
|---|---|
| 紙の帳簿（仕訳帳・総勘定元帳・試算表） | 情報は**罫線の表**。金額は右にそろう。見出しは二重線。色はほぼ墨1色で、印だけに朱 |
| 役所の申請書・通知 | 1画面に、やることは1つ。ボタンは1つ。飾りの絵がない |

### 8.2 決めること（3つだけ）
| 項目 | 値 |
|---|---|
| 色 | 紙 `#F7F3E8`（背景）・墨 `#2B2A28`（文字）・藍 `#2F4F7A`（ボタンとリンクだけ）。印として、朱 `#A8322C`（止める・しないこと）、薄緑 `#DCE5D3`（確かめた行の地）、鼠 `#5F5A50`（補足） |
| 文字 | 本文は `"BIZ UDPGothic", "Hiragino Sans", "Yu Gothic UI", sans-serif`。見出しと帳簿の題は `"BIZ UDPMincho", "Hiragino Mincho ProN", "Yu Mincho", serif`。**外からフォントを読み込まない**。本文16px以上 |
| 余白と角 | 余白は広め。角は 2px。影は使わない |

コントラスト（計算済み。テストでも確かめる）：墨／紙 12.9、白／藍 8.3、藍／紙 7.5、朱／紙 6.0、鼠／紙 6.2、墨／薄緑 11.1。すべて 4.5 以上。

### 8.3 やらないこと
グラデーション、絵文字、意味のないヒーロー画像と「ようこそ」、影、角の丸いカードの並び、飾りのアニメーション、仮の文（「ここに文章が入ります」）、色を足すこと、理由なく情報を枠で囲むこと。

### 8.4 形の決まり
- **比べるものは表**（候補3人、訪問、明細、台帳、地域の集計）。候補は「カード3枚」にしない。行で並べ、理由を同じ列にそろえる。
- 金額と時間は右そろえ・等幅の数字（`font-variant-numeric: tabular-nums`）。合計の行は二重線。
- 1画面に、主のボタンは1つ。「しないこと」は朱の罫で囲む（唯一の枠）。
- 色とフォントは `theme.css` の Pico の変数（`--pico-…`）だけで変える。クラスはなるべく足さない。`<html lang="ja" data-theme="light">` で明るい配色に固定する。

### 8.5 状態（必ず作る）
| 状態 | 決まり |
|---|---|
| 空 | 「まだ困りごとはありません」と、次の1手のボタン |
| 長い文字 | 事務所名・困りごとが長くても、表が崩れない（折り返す） |
| エラー | その欄のすぐ下に、何が違うかを書く |
| フォーカス | Tab キーで、今いる所が見える |
| 押せない | 職員に見える「公開する」は、押せない見た目と、理由の1行 |
| 伏せ字 | 5件未満は「―（少ないため表示しません）」 |
| スマホ | 幅360pxで横にはみ出さない。表は、列を減らすか、横に送れる |

決まりは `AGENTS.md` の「デザイン」に6行で書き、毎回読ませる。増えたら `docs/DESIGN.md` に分ける。

## 9. AI（既定は使わない）

| 項目 | 決まり |
|---|---|
| 使う場面 | F91 だけ。規則（辞書）で訳せなかった困りごとに、型の**候補**を出す |
| 使わない場面 | マッチング・料金・台帳・税理士業務の見分け。**これらは規則だけ** |
| 切り替え | `AI_DRIVER=off`（既定）／`fake`（テスト）／`claude`。呼ぶのは `app/services/ai.py` の1か所だけ |
| 送る前 | 名前・社名・金額・電話・メールアドレスの形を伏せる。伏せた後の文章だけを送る |
| 受け取り | 型は Enum にあるものだけ採る。無い値は捨てる。採るかどうかは、人が画面で決める |
| 記録 | 呼んだ日時・伏せた後の文字数・結果・費用の目安。本文は残さない |
| 費用 | `AI_MONTHLY_LIMIT_YEN` を超えたら、その月は `off` と同じに動く。鍵はログインした人の操作でしか使われない |
| モデル | `.env` の `AI_MODEL`。作るときに公式の文書で確かめる（設計書に名前を書き写さない） |
| テスト | `fake` だけを使い、本物は呼ばない |

## 10. 検査

### 10.1 コミットの前に毎回
```bash
make check        # ruff format --check → ruff check → mypy → pytest
```
1つでも落ちたら、コミットしない。

### 10.2 テストの置き方
| 種類 | 場所 | DB | 目安 |
|---|---|---|---|
| 規則 | `tests/rules/` | なし | 1秒以内。境界の値（0時間・半分ちょうど・同点・5件ちょうど）を必ず入れる |
| 権限 | `tests/app/test_authz_*.py` | SQLite | 拒否のマス1つにつき1本 |
| 状態 | `tests/app/test_*_flow.py` | SQLite | 状態の遷移と、許されない遷移 |
| 設定 | `tests/app/test_config.py` | — | §5.2 の検査が、足りないとき止まること |
| 見た目 | `tests/app/test_design.py` | — | `theme.css` の色の組み合わせのコントラストが 4.5 以上 |

テスト番号（T…）は 03 §10。機能を足したら、同じコミットでテストを足す。

### 10.3 CI（`.github/workflows/ci.yml`）
push と PR で動く。①`make check`（SQLite）②MySQL のサービスを立てて全テスト（`TEST_DATABASE_URL`）③空の MySQL に `flask db upgrade` が通る ④秘密の形をした文字列・`.env` が入っていないかの確認。

**公開の前に、MySQL で全テストを通す。** SQLite で通っても、MySQL で落ちることがある（文字の長さ・ロック・日時）。

## 11. 公開

### 11.1 構成
```
スマホ・PC ──HTTPS──> nginx（443。80 は 443 へ） ──> gunicorn（127.0.0.1:8000・2プロセス） ──> Flask
                                                                └─> MySQL（127.0.0.1:3306。外に開けない）
毎日 3:30  backup.sh ──> サーバーの中（7日） ──> 別の会社のオブジェクトストレージ（30日）
```

### 11.2 置き場所の比べ方
| 置き場所 | この構成が動くか | 月額の目安 | 判断 |
|---|---|---|---|
| **Xserver VPS**（Ubuntu 24.04） | 動く（root 権限あり） | 2GB：1,980円（1か月契約）／1,540円（12か月契約）。4GB：2,640円／2,200円（2026年9月の改定後・税込） | **第一候補。** 発表のあとも置き続けられる |
| エックスサーバー（共用のレンタルサーバー） | **動かない**（root 権限がなく、gunicorn・nginx・systemd を自分で置けない） | — | 契約しない。名前が似ているので、申し込みで間違えない |
| Google Cloud の VM を新しく作る（キャンプの手順） | 動く | 無料の試用の間は0円 | **代わりの案。** 手順は教材のまま。ただし試用が終わると止まり、30日後に消える |
| キャンプの VM に同居（教材11章） | 動く | 0円 | 使わない。ポートと nginx の書き換えで、動いているアプリを壊すおそれ（講師の助言） |

- Xserver VPS は 2026年9月に新規の受付が止まり、10月6日に再開した。**2GB のプランを今申し込めるかは、申し込みの画面で確かめる。** 無ければ 4GB。
- 最初は1か月契約にする（合わなければやめられる）。続けると決めてから、長い契約に変える。
- パケットフィルターを ON にし、足すのは「SSH」と「Web」（80・443）だけ。サーバーの中の ufw と**両方が効く**ので、両方で開ける（メニューの名前は公式マニュアルで確かめた。runbook §2.1）。
- HTTPS にはドメインが要る（今ある自分のドメインのサブドメインが早い）。無い間は、IP を含む名前（sslip.io）で証明書を取る。
- 手順は [runbook.md](runbook.md)。**サーバーの操作は本人がする。AI エージェントは本番に SSH しない。**

### 11.3 サーバーの中の決まり
| 項目 | 値 |
|---|---|
| 置き場所 | `/var/www/tsunagudake-app`（キャンプと同じ形） |
| 動かすユーザー | 一般ユーザー（root で動かさない） |
| 起動 | systemd `tsunagudake.service`：`.venv/bin/gunicorn -c deploy/gunicorn.conf.py "app:create_app()"`（起動のたびに uv を通さない。127.0.0.1:8000・2プロセス）。守り（`NoNewPrivileges` など）は、動いてから1行ずつ足す（runbook §8.1） |
| 設定 | `/var/www/tsunagudake-app/.env`（`chmod 600`） |
| DB | MySQL（Ubuntu の apt）。DB とユーザーは `tsunagudake`。パスワードは `openssl rand -hex 24` |
| nginx | `client_max_body_size 1m`（アップロードが無い）。`X-Forwarded-For`（上書き）・`X-Forwarded-Proto` を渡す。`/static/` は nginx が返す。隠しファイル（`.env`・`.git`）は 403。版を出さない |
| 証明書 | certbot（自動で更新） |
| 入口 | SSH は鍵だけ。root でのログインを止める。ufw とパケットフィルターの両方で、22・80・443 だけ |

### 11.4 反映（F67）
main に入る → `ci.yml` が通る → `deploy.yml` が SSH で `deploy.sh` を動かす。
`deploy.sh`：①DB をバックアップ ②`git pull` ③`uv sync --frozen --no-dev` ④`flask db upgrade` ⑤`sudo systemctl restart tsunagudake` ⑥`/healthz` が 200 を返すまで待つ。返らなければ失敗として知らせる。
再起動だけを sudo で許す（`/etc/sudoers.d/` に1行）。**サーバーの上でファイルを直さない**（次の `git pull` でぶつかる）。

### 11.5 バックアップ（F66）
**戻せて初めて、バックアップ。**

| 決めること | 値 |
|---|---|
| 失ってよい量 | 最大24時間分（実際の事務所が使う前に見直す） |
| 置き場所 | ①サーバーの中（7日）②**別の会社**のオブジェクトストレージ（30日。S3 互換。キャンプ12章の Cloud Storage のバケット）③手元の PC（月1回） |
| 鍵 | バックアップ専用の鍵。アプリの `.env` と別のファイル（`~/.config/tsunagudake-backup/s3.env`、`chmod 600`）。アプリには渡さない |
| 消されたとき | バケットの「削除（復元可能）」が有効なことを、1度確かめる |
| 中身 | `mysqldump --single-transaction --no-tablespaces`。アップロードが無いので、DB だけ |
| 名前 | 日付と時刻まで入れる（同じ日に2回動かしても、良いものを上書きしない） |
| 確かめ | 月1回、別の DB に戻して件数を比べる（AC13）。`gzip -t` で壊れていないか |
| 事故のとき | **バックアップのスクリプトを動かさない。** 先に退避する。削除・上書き・復元は、人がする |

VPS と バケットが別の会社なので、片方の契約が止まっても、もう片方は残る（教材の盲点1の対策）。

## 12. 開発の道具（Claude Code など）

決める順番は、**AGENTS.md → スキル／サブエージェント → MCP**。足す前に、要るかを確かめる。

| 道具 | 置くもの | いつ |
|---|---|---|
| `AGENTS.md`（`CLAUDE.md` は読み込むだけ） | 前提・コードの決まり・コマンド・進め方・触ってよい範囲 | 段階0 |
| スキル `check` | `make check` を流して結果を報告 | 段階0 |
| サブエージェント `authz-checker`（`Read, Grep, Glob` だけ） | ルートの一覧と authorization.md を見比べ、権限の確かめが無い入口・テストの無いマスを、ファイルと行で返す。**直さない** | 段階3の後（同じ点検を3回やったら作る） |
| サブエージェント `ja-checker`（`Read, Grep, Glob` だけ） | 英語のままの文言・敬体と常体の混在・表記の揺れを一覧で返す。**直さない** | 段階9の前 |
| MCP（自作・stdio・読み取り専用） | F90。手元の開発用 DB だけ。道具は3つまで（公開中の困りごとの件数、台帳の検証、候補の理由）。**返す列を固定し、メールアドレスと本文を返さない** | P2。`sqlite3` と `flask shell` で足りる間は作らない |

決まり：
- 点検と修正を分ける。サブエージェントは見つけるだけ、直すのは本体、決めるのは人。
- サブエージェントの `tools` は必ず書く（省くと全部の道具を引き継ぐ）。
- MCP は本番の DB につながない。本番のバックアップも使わない。`.mcp.json` に鍵を書かない。
- 本番のサーバーで AI にコマンドを打たせない。候補を出させ、**自分で読んで、自分で打つ**。ログを貼る前に、鍵が入っていないかを見る。

## 13. 手本のリポジトリとの対応

「同じ水準」を、項目で確かめられるようにする。

| 手本がやっていること | このアプリでは | 段階 |
|---|---|---|
| uv・`pyproject.toml`・`uv.lock`・pytest | 同じ。加えて Ruff・mypy・`make check` | 0 |
| `.env.example` に項目の一覧と説明。`.gitignore` で `.env` と DB を除く | 同じ（§5.3）。加えて CI で秘密の混入を確かめる | 0 |
| `APP_ENV` 未設定なら本番。足りない設定で起動しない | 同じ（§5.1・§5.2） | 0 |
| 開発は SQLite、本番は MySQL。公開前に MySQL で全テスト | 同じ（§10.3）。CI でも毎回 | 0 |
| `session_nonce` で、パスワード変更後に前のログインを切る | 同じ | 3 |
| 管理者はコマンドでだけ作る | 全員をコマンドで作る（招待の画面は P2） | 3 |
| 所有者の確かめ | 事務所から引く＋権限表＋拒否のマスごとのテスト（[authorization.md](authorization.md)） | 3〜8 |
| 状態を確かめてから更新する1回の UPDATE（同時の操作への備え） | 同じ考え（03 §7） | 4〜7 |
| 回数制限（相手はハッシュで保存） | 同じ（§7） | 12 |
| デモ環境（上部の表示・`seed-demo`・`reset-demo`） | 同じ。加えて「デモで入る」（F62） | 9 |
| VM への設置の手順書・systemd・nginx・バックアップ | [runbook.md](runbook.md)。加えて、別の会社へのバックアップと復元の練習 | 1・10 |
| 色はスタイルシートの先頭で決める。コントラストをテストで確かめる | 同じ（§8） | 0・14 |
| 仕様書の「決定済み」「未決」の表 | 01 §12・この文書 §14・03 §13 | — |
| 決済（Stripe）・Word の出力・画像のアップロード | **作らない**（要らないものは、持たない） | — |

## 14. 未決

| # | 決めること | いつまで |
|---|---|---|
| U1 | ドメイン（今あるドメインのサブドメインか、新しく取るか） | 段階1 |
| U2 | Xserver VPS のプラン（2GB を申し込めるか） | 段階1 |
| U3 | 本番のメールの送り方（SMTP・配信サービス） | 段階16 |
| U4 | 2段階認証のライブラリ | 段階17 |
| U5 | 市町村の間の移動時間の出どころ（発表は架空の表。実際は手で入れるか、地図のサービスか） | 実際の事務所が使う前 |
| U6 | プライバシーポリシー・利用規約の文面 | 実際の事務所が使う前 |
| U7 | 公開デモの共有アカウントは、発表時に案内する合言葉で利用できる。発表直前に `reset-demo` を実行する。 | 段階9 |
