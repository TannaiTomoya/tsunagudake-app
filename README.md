# つなぐだけ（Tsunagudake）

**税理士は、判断するだけ。**
地方の会計事務所に、会計とITの両方がわかる人（つなぎ手）を週1回入れる仕組み。困りごとを自分の言葉で書くと、規則が「会計の業務」と「ITの作業」に訳し、言葉が通じて毎週通える人を、理由つきで3人まで出す。成果は事務所が確かめた「減った時間」だけで数え、**減らなければ0円**。依頼と合意は、書き換えられない台帳に残す。

八幡平市スパルタキャンプ 2026年度 Python編の最終制作。姉妹アプリは [撮って送るだけ帳簿（torudake）](https://github.com/TannaiTomoya/torudake-app)。

> 原点は、母のため。真面目に記録する人が、言った・言わないの中で損をしないように、**記録で境界線を引く**。
> 撮って送るだけ帳簿は会社の**中**で経理の人を守り、つなぐだけは会社の**外**で支える人をつなぐ。

---

## 今の状態（2026-11-19）

| もの | 状態 |
|---|---|
| 設計書（要件・技術・詳細・権限・公開の手順） | **済み**（`docs/`） |
| 作る順番 | **済み**（[Issues](https://github.com/TannaiTomoya/tsunagudake-app/issues)。段階0から順に並んでいる） |
| アプリのコード | **これから**（段階0から）。下の「開発の始め方」は、段階0が済むと動く |
| 公開 | これから（段階1） |

発表（2026-11-22）で見せるのは、P0（段階0〜9）。数字と名前は、すべて架空。

## 90秒でわかる流れ

| 立場 | 場面 | 規則 |
|---|---|---|
| 会計事務所 | 困りごとを書く：「顧問先の資料が月末まで集まらない。試算表を過去と見比べるのに、毎月30時間かかる」 | — |
| 会計事務所 | 翻訳：「資料回収」「チェックと比較」。月の価値 30時間 × 4,000円 ＝ 12万円。「申告書の作成」が入っていれば、受け付けない | `translate`・`estimate_value` |
| 会計事務所 | 候補は最大3人。1人ずつ理由つき。誓約が切れた人・60分を超える人・曜日が合わない人は、最初から出ない | `match` |
| つなぎ手 | 声かけを受ける → 合意。台帳に「しないこと：税理士業務・顧問先の生データの持ち出し」が残る | `ledger` |
| 会計事務所 | 1か月後：減った時間20時間 → 価値8万円 → 料金3.2万円（40%）。0時間なら0円 | `calc_fee` |
| 自治体 | 町の集計（匿名）。事務所が5つに満たない町は、伏せる | `mask_region` |

**同じ入力なら、必ず同じ答え。** マッチングと料金に AI は使わない。規則の中身は、アプリの「しくみ」のページで全部見せる。

## ドキュメント

| 文書 | 中身 |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | 要件定義書：原点と目的、聞き取りで分かったこと、ロール、機能 F01〜F91、画面、非機能、法律、完成の基準 AC01〜AC14、台本、決定済みと確かめること |
| [docs/02-technical-spec.md](docs/02-technical-spec.md) | 技術仕様書：技術の選び方と理由、構成、フォルダ、設定と起動時の検査、安全、見た目、AI、検査、公開、開発の道具 |
| [docs/03-detailed-design.md](docs/03-detailed-design.md) | 詳細設計書：テーブル、規則（式と例）、状態と遷移、同時の操作への備え、ルート、テスト T01〜T35、デモのデータ、**作る順番（§12）** |
| [docs/authorization.md](docs/authorization.md) | 権限表（正本）。誰が・何を・できるか。拒否のマスごとにテストを書く |
| [docs/runbook.md](docs/runbook.md) | 公開の手順（Xserver VPS・Ubuntu 24.04・MySQL・nginx・gunicorn・HTTPS・バックアップ） |
| [AGENTS.md](AGENTS.md) | AI エージェント（Claude Code など）が従う決まり |

## 構成

| 層 | 使うもの |
|---|---|
| 言語・管理 | Python 3.14・uv（`pyproject.toml`・`uv.lock`） |
| Web | Flask 3.1・Flask-Login・Flask-WTF・Flask-SQLAlchemy・Flask-Migrate |
| DB | 開発は SQLite、demo・本番は MySQL（PyMySQL） |
| 規則 | `tsunagu_rules/`（Flask も DB も知らない純粋な関数。翻訳・価値・マッチング・料金・台帳・地域） |
| 見た目 | Pico CSS 2 ＋ `theme.css`（紙・墨・藍の3色。外からの読み込みなし） |
| 検査 | Ruff・mypy・pytest（`make check`）・GitHub Actions |
| 公開 | VPS（Ubuntu 24.04）・nginx・gunicorn・systemd・Let's Encrypt |

```
app/            Flask のアプリ（blueprints・models・services・templates・static）
tsunagu_rules/  規則（ここが Python で作った中身）
tests/rules/    規則のテスト（DB なし）    tests/app/  画面・権限・状態のテスト
migrations/     DB の変更の履歴
deploy/         nginx・systemd・deploy.sh・backup の雛形（秘密は入れない）
docs/           設計書
```

作らないもの：決済、ファイルのアップロード、顧問先のデータの保管、AI によるマッチングの確定（理由は 02 §2）。

## 開発の始め方

段階0（[Issues](https://github.com/TannaiTomoya/tsunagudake-app/issues) の最初の1つ）が済むと、次のとおりに動く。

```bash
git clone git@github.com:TannaiTomoya/tsunagudake-app.git
cd tsunagudake-app
uv sync                          # 必要なものを入れる（Python も uv が入れる）
cp .env.example .env             # .env は Git に入れない
uv run flask db upgrade          # DB を作る（開発は SQLite。instance/ の下にできる）
uv run flask seed-demo           # 架空のデータを入れる
uv run flask run --debug         # http://127.0.0.1:5000
make check                       # 整形 → 静的解析 → 型 → 全テスト（コミットの前に毎回）
```

| コマンド | すること |
|---|---|
| `uv run pytest tests/rules -q` | 規則だけを試す（1秒以内） |
| `uv run flask create-user` | 人を作る（画面からは作れない） |
| `uv run flask reset-demo --yes` | 架空のデータを消して入れ直す（demo・development だけ） |
| `uv run flask verify-ledger` | 境界線台帳が書き換えられていないかを確かめる |
| `TEST_DATABASE_URL=… uv run pytest` | MySQL で全テスト（公開の前に必ず） |

## 設定（`.env`）

**`.env` は Git に入れない・人に見せない・チャットや AI に貼らない。** 項目の一覧は [`.env.example`](.env.example)（値は空）。

| `APP_ENV` | 使う場面 | DB | 足りない設定があるとき |
|---|---|---|---|
| `development` | 手元 | SQLite | 開発用の固定の鍵で動く |
| `demo` | 発表（公開。データは架空） | MySQL | **起動しない** |
| `production` | 実際の事務所が使う | MySQL | **起動しない** |

- `APP_ENV` が未設定なら `production` として動く（設定を忘れたサーバーを、甘い設定で公開しないため）。
- demo・production は、鍵が空・SQLite・http のどれかで、理由を出して止まる。
- 「デモで入る」と `seed-demo`・`reset-demo` は、production では動かない。

`.gitignore` は、秘密（`.env`・鍵・`.my.cnf`・`s3.env`）、データ（`instance/`・`*.sqlite`・`*.sql.gz`・`backup/`）、作業ファイルを除く。`uv.lock` と `.env.example` は入れる。コミットの前に `git status` で目で確かめ、`git add .` は使わない。

## 守り

| 守るもの | やり方 |
|---|---|
| 税理士の仕事 | 税務代理・税務書類の作成・税務相談にあたる困りごとは、入口で止める。合意のたびに「しないこと」として台帳に残る（外せない） |
| 顧問先の秘密 | 顧問先の生データを、アプリに置かない。つなぎ手は、守秘の誓約と研修が有効な人だけ（1年ごとに更新） |
| 事務所どうし | 他の事務所のものは 404（あることも見せない）。権限表のマスごとにテスト |
| 言った・言わない | 依頼・合意・変更・完了・指摘は、追記だけの台帳に。前の行のハッシュをつなぎ、書き換えが見つかる |
| 町の集計 | 匿名。5件未満は伏せる。引き算で分かる合計も伏せる |
| 秘密の設定 | `.env` は Git に入れない。本番は起動時に検査。CI で混入を確かめる |

法律（税理士法・職業安定法・労働者派遣法・フリーランス法・個人情報保護法）に関わる所は、**実際の事務所が使う前に、専門家に確かめる**（01 §9.3）。今は、発表用の架空のデータだけで動かす。

## 公開

Xserver VPS（Ubuntu 24.04）に、nginx ＋ gunicorn ＋ MySQL ＋ HTTPS で置く。手順は [docs/runbook.md](docs/runbook.md)。
共用のレンタルサーバーでは、この構成は動かない。サーバーの操作は本人がする（AI エージェントは本番に SSH しない）。

## 進め方

- 1つの Issue ＝ 1つの段階。1つずつ進め、終えるたびに `make check` を通してコミットする。
- 機能を足したら、同じコミットでテストを足す（番号は 03 §10）。
- 入口（ルート）を足すときは、先に [権限表](docs/authorization.md) にマスを足す。
- 実在の事務所・人・数字は、リポジトリ・テスト・デモのデータに入れない（このリポジトリは公開）。
