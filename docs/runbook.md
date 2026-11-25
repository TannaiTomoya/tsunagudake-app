# 公開の手順（Xserver VPS） — つなぐだけ（Tsunagudake）

版1.5・2026-11-25。構成と理由は [02 技術仕様書 §11](02-technical-spec.md)。

**この手順は、本人が自分で打つ。** AI エージェントは本番に SSH しない。分からない所は、候補を出させて、読んでから自分で打つ。
前提：XServer VPS クラウド（XServer VPS は新規受付を止めていた）、Ubuntu 26.04、DB は VPS の中の MySQL、HTTPS、ドメインあり。Ubuntu 24.04 でも、同じ手順で進められる（版1.1 で確かめた）。
`example.jp`・`203.0.113.10` は、自分のものに読み替える。

---

## 確かめた記録（版1.3）

版1.0 は、1行も実行せずに書いていた。版1.1 で、Ubuntu 24.04 の上で実際に動かして確かめ、見つかった不具合を直した。
版1.2 で、**Ubuntu 26.04 の上で、同じ手順をこのアプリで動かして**確かめ直した。下の表で「確認済み」の所は、手順のコマンドを変えずに通った。
確かめた場所は、手元の `ubuntu:26.04` のコンテナ（arm64。systemd は動いていない）。VPS は x86_64 で、最初から入っているものも違う。
版1.3 で、**XServer VPS クラウドの実機（Ubuntu 26.04・x86_64）で §1〜§10 を上から順に打ち**、発表用のデモを https で公開した（2026-11-20）。表の「実機で確認」が、そのときの結果。
残りの「実機」と書いた所は、まだ VPS の上で確かめていない。

| 節 | 何を | どう確かめたか（Ubuntu 26.04） | 結果 |
|---|---|---|---|
| 1 | 申し込み・OS・SSH Key | XServer VPS は新規受付を止めていたので、VPS クラウドで申し込んだ。Ubuntu 26.04 を選べた。SSH Key は「キーを作成する」→「インポート」で自分の公開鍵を登録できた | **実機で確認**（注意10） |
| 2 | SSH の設定ファイルの効き方 | OpenSSH 10.2 で `sshd -T`。`99-` では止まらず、`00-` で止まる | 確認済み（24.04 と同じ。注意1） |
| 2 | パケットフィルター | 作っただけでは効かず、SSH が時間切れになった。VPS の「ネットワーク」タブで適用して通った | **実機で確認**（注意11） |
| 2 | `adduser`・`ufw`・鍵でのログイン・`systemctl restart ssh` | §2.2〜§2.4 のとおりに通った。`sshd -T` は2行とも `no` | **実機で確認** |
| 3 | パッケージ | §3 の1行をそのまま実行。nginx 1.28.3・MySQL 8.4.13・certbot 4.0.0 が入った | 確認済み（注意7） |
| 3 | uv のインストーラー | 実行して、`uv --version` が通った。`uv sync --frozen --no-dev` で Python 3.14 が入った | 確認済み |
| 4 | DB とユーザーを作る SQL・待ち受けるアドレス・文字コード | MySQL 8.4.13 で実行 | 確認済み |
| 4 | アプリからの接続（行のロック・CHECK 制約・同時実行） | MySQL 8.4.13 で `flask db upgrade` と全テスト（417件） | 確認済み |
| 7 | `flask db upgrade`・`flask seed-demo`・`flask verify-ledger` | MySQL 8.4.13 で実行。`MySQLImpl` と出た | 確認済み |
| 7 | gunicorn の起動と設定ファイル | gunicorn 27.0 でこのアプリを起動。`/healthz` が `ok` | 確認済み |
| 8 | systemd の設定と起動 | `enable --now` で `active`、`/healthz` が `ok` | **実機で確認** |
| 9 | nginx の設定 | nginx 1.28.3 で `nginx -t`。nginx 越しに `/healthz` が 200、偽の IP が通らない、知らない Host が 400、隠しファイルが 403、`/static/` が 200 | 確認済み（注意2・3） |
| 5 | `git clone` の権限 | `umask 077` のまま取ってきたら、nginx が `/static/` を読めず 403 になった | **実機で確認**（注意12） |
| 9・10 | nginx と証明書 | 初期の `default` が残ったまま certbot を打つと、証明書が初期ページにつき、トップが「Welcome to nginx!」になった。名前を後から足すと、証明書が置き換わった | **実機で確認**（注意13・14） |
| 10 | 証明書（certbot）・ドメイン | Let's Encrypt で取れた。`certbot.timer` も入った | **実機で確認** |
| 11 | 再起動の後も動くか | — | **実機** |
| 12 | `deploy.sh` | 控え → 早送り → `uv sync` → `flask db upgrade` まで通った | **再起動と `/healthz` の確認は実機** |
| 12 | 再起動だけを許す sudoers の1行 | sudo-rs 0.2.13 で `visudo -c`・`sudo -l -U ubuntu`。許した再起動だけが sudo を通り、ほかは断られた | 確認済み（注意8） |
| 13 | バックアップと復元 | `backup.sh` → `gzip -t` → 別の DB に戻す → 件数と、文字の中身（16進）が同じ | 確認済み（注意4） |
| 13 | サーバーの外へ送る所 | 送り先が無いと、失敗として終わり、サーバーの中の控えは残る | 送る所は**実機**（段階10） |

見つけて直したこと（版1.1・Ubuntu 24.04）：
1. **SSH の設定は、番号の小さいファイルが勝つ。** `99-…conf` に「パスワードでのログインを止める」と書いても、VPS に最初からある `50-cloud-init.conf` が「許す」と書いていると、**止まらない**（実際に確かめた）。ファイル名を `00-…conf` にし、`sshd -T` で効いていることを確かめる手順にした。
2. nginx は、相手の IP を `X-Forwarded-For` に**上書きして**渡す（相手が付けてきた値を引き継がない）。前の書き方でも、アプリ側の設定で偽の値は通らなかったが、アプリの設定に頼らない形にした。
3. IPv6 が無いサーバーでは、`listen [::]:80;` の行で nginx が起動しない（「困ったとき」に足した）。
4. `mysqldump` は `--no-tablespaces` を外すと、権限のエラーで止まる。日本語を画面で見るときは `--default-character-set=utf8mb4` を付ける（付けないと `???` と出ることがある。データは壊れていない）。
5. アプリの起動は、`uv run …` ではなく `.venv/bin/gunicorn` を直接呼ぶ形にした（起動のたびに uv を通さない）。
6. `dig` は最初から入っていないことがある（`dnsutils` を足した）。

Ubuntu 26.04 で変わっていたこと（版1.2。手順は変えていない）：

7. 入る版が上がった：nginx 1.24 → 1.28、MySQL 8.0 → 8.4、OpenSSH 9.6 → 10.2、systemd 255 → 259。`dnsutils` は名前だけが残り、入るのは `bind9-dnsutils`（§3 の1行は、そのまま通る）。
8. `sudo` は sudo-rs、`stat`・`date`・`mktemp` は Rust で書き直された版（uutils）に替わった。`deploy.sh`（再起動の手前まで）・`backup.sh`（送る手前まで）・sudoers の1行は、そのまま動いた。
9. MySQL 8.4 は、古い認証（`mysql_native_password`）が最初から無効。§4 で作るユーザーは新しい認証（`caching_sha2_password`）になり、アプリからつながった。

実機で公開したときに分かったこと（版1.3）：

10. **XServer VPS は新規の申し込みを止めていた。** 申し込めたのは「XServer VPS クラウド」。メモリは 4GB から。SSH Key は申し込みの画面で「キーを作成する」→「インポート」を選び、自分の PC で作った公開鍵（`.pub`）を貼る。
11. **パケットフィルターは、作っただけでは効かない。** 「パケットフィルター」でルールを作ったあと、「VPS」→ 対象のサーバーの「詳細」→「**ネットワーク**」タブで適用する。適用するまでは、22 番も含めて外からの通信がすべて止まり、`ssh` が時間切れになる。
12. **`umask 077` のまま `git clone` すると、nginx が見た目のファイルを読めない。** フォルダが `drwx------` になり、`/static/` が 403 になる。§6 で `.env` を作るときだけ `umask 077` にし、すぐ `umask 022` に戻す。なってしまったら §9 の直し方。`deploy.sh` も控えを取るときだけ `umask 077` にし、早送りの前に `umask 022` に戻して、`app/static` の権限を直す（版1.3 までの `deploy.sh` は 077 のまま早送りし、書き換わった CSS と JS が 403 になった。実機で確認）。
13. **初期の `sites-enabled/default` を外してから certbot を打つ。** 残っていると、証明書が初期ページにつき、https のトップが「Welcome to nginx!」になる。
14. **1つの設定に名前を後から足して certbot を打つと、証明書が新しい名前だけのものに置き換わり、古い名前が証明書のエラーになる。** 2つ以上の名前で開けるようにするときは、`--cert-name` で1枚にまとめる（§10）。

---

## 0. 全体の流れ
1. VPS を申し込む　2. 最初の守り　3. 必要なものを入れる　4. MySQL　5. アプリを置く　6. 設定（`.env`）　7. DB を作る　8. 自動起動　9. nginx　10. HTTPS　11. 確かめる　12. 更新　13. バックアップ　14. 発表の直前　15. やめるとき　16. OS を入れ直すとき　17. torudake と同じサーバーに置くとき

待ち時間があるのは、1（申し込み）と10（ドメインの向き先が広まるまで）。**ここを先にやる。**

## 1. VPS を申し込む
- 申し込むのは **「XServer VPS」**。名前の似ている「エックスサーバー（レンタルサーバー）」では、この構成は動かない。**XServer VPS が新規受付を止めていたら「XServer VPS クラウド」**（注意10）。申し込みの最初の画面では「VPS（Linux系サーバー）」を選ぶ（「おまとめ自動セットアップ」やオプションは選ばない）。
- プラン：メモリ 2GB（申し込めなければ 4GB）。契約は**1か月**から。確定の前に、支払いの内訳で期間とディスクを確かめる（選び間違えると12か月分になっていることがある）。
- OS：Ubuntu 26.04（選べなければ 24.04。手順は同じ）。アプリのテンプレートは選ばない。
- root のパスワードは長いランダムなものにし、パスワード管理の道具に入れる。
- **SSH Key は、申し込みのときに登録する。** 自分の PC で `ssh-keygen -t ed25519 -f ~/.ssh/tsunagudake_vps -C apps-01 -N ""` で作り、`~/.ssh/tsunagudake_vps.pub` の1行を「インポート」に貼る（秘密鍵はどこにも貼らない）。公式マニュアルでは、設定できるのは「お申し込み時、またはOS再インストール時」。登録しなかった場合は、root のパスワードで入ってから、2の手順で鍵を置く。
- 控えるもの：IP アドレス（VPS パネルのトップページ）。
- ドメインの A レコードを、この IP に向ける（例：`tsunagudake.example.jp`）。

## 2. 最初の守り

### 2.1 パケットフィルター（VPS パネル）
**VPS クラウドの場合**（実機で確認）：
1. 「パケットフィルター」→「パケットフィルターを作成する」（名前の例：`apps-01-filter`）。
2. その「詳細」→「ルールを追加する」で、TCP・IPv4・ポート 22〜22・接続元 `0.0.0.0/0` を足す。80 と 443 も同じように足す。
3. **「VPS」→ 対象のサーバーの「詳細」→「ネットワーク」タブで、作ったフィルターを適用する**（注意11）。

**XServer VPS の場合**（公式マニュアルから）：
1. 左のメニューの「パケットフィルター設定」を開く。
2. 「ONにする（推奨）」を選び、「変更する」→「OK」。
3. 「パケットフィルター設定を追加する」→ フィルターで「SSH」を選び、「追加する」。
4. 同じように、フィルターで「Web」を足す（80 と 443）。「Web」が無ければ「手動で設定」で、TCP の 80 と 443 を足す。

ON にすると、足したものだけが通る。OFF は、すべてが通る（使わない）。パケットフィルターと、サーバーの中のファイアウォール（ufw）は**両方が効く**ので、両方で開ける。

### 2.2 ふだん使うユーザーと鍵
```bash
# 自分の PC から（最初だけ root）
ssh root@203.0.113.10

apt update && apt -y upgrade
timedatectl set-timezone Asia/Tokyo

# ふだん使うユーザー（キャンプの教材と同じ名前にすると、教材のコマンドがそのまま使える）
id ubuntu || adduser ubuntu          # すでにあれば、作らない
usermod -aG sudo ubuntu
mkdir -p /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh
# 申し込みのときに鍵を登録したなら、root の鍵を写すのが早い（貼り間違いがない）
cp /root/.ssh/authorized_keys /home/ubuntu/.ssh/   # 登録しなかったときは nano で .pub を1行貼る
chmod 600 /home/ubuntu/.ssh/authorized_keys
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
```

**自分の PC で別のターミナルを開き、`ssh ubuntu@203.0.113.10` で入れることと、`sudo -v` が通ることを確かめてから**、次に進む（確かめる前に閉じると、入れなくなる）。サーバーの中（`root@…#` のタブ）で `ssh` を打たない。
`apt -y upgrade` で新しいカーネルが入ったら、§2.4 のあとに `sudo reboot` し、`uname -r` で切り替わったことを見る。

### 2.3 root とパスワードでのログインを止める
```bash
# ubuntu で入ってから。ファイル名は 00- で始める（番号の小さいファイルが勝つ）
sudo nano /etc/ssh/sshd_config.d/00-tsunagudake.conf
```
```
PermitRootLogin no
PasswordAuthentication no
```
```bash
sudo sshd -t && sudo systemctl restart ssh
# 効いていることを確かめる。2行とも no と出ること
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '
```
`passwordauthentication yes` と出たら、ほかのファイルが勝っている。`ls /etc/ssh/sshd_config.d/` で名前を見て、自分のファイルが一番先に来る名前にする。

### 2.4 サーバーの中のファイアウォール
```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

自分の PC の `~/.ssh/config` に、別名を足しておく（以降は `ssh tsunagu-vps`）。
```
Host tsunagu-vps
    HostName 203.0.113.10
    User ubuntu
    IdentityFile ~/.ssh/自分の鍵
```

## 3. 必要なものを入れる
```bash
sudo apt -y install nginx mysql-server git curl certbot python3-certbot-nginx dnsutils
curl -LsSf https://astral.sh/uv/install.sh | sh
```
uv のインストーラーが、最後に「これを打ってください」と表示するコマンドを打つ（または、SSH を入り直す）。そのあと `uv --version` で確かめる。

## 4. MySQL
```bash
sudo systemctl enable --now mysql
openssl rand -hex 24          # パスワード。控える（人に見せない・どこにも貼らない）
sudo mysql
```
```sql
CREATE DATABASE tsunagudake CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'tsunagudake'@'localhost' IDENTIFIED BY 'ここにパスワード';
GRANT ALL PRIVILEGES ON tsunagudake.* TO 'tsunagudake'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```
```bash
mysql -u tsunagudake -p tsunagudake -e "SELECT CURRENT_USER(), @@bind_address;"
```
`tsunagudake@localhost` と `127.0.0.1` が出ればよい。MySQL は外に開けない（3306 番は、ufw でもパケットフィルターでも開けない）。

## 5. アプリを置く
**`umask` が `022` のときに取ってくる**（注意12）。リポジトリが公開なら、Deploy key を作らずに https で取ってこられる（`git clone https://github.com/TannaiTomoya/tsunagudake-app.git`）。非公開にするときだけ、下の鍵を使う。
```bash
# GitHub から読み取るための鍵（このサーバー専用。リポジトリを非公開にするとき）
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy_tsunagudake -C "tsunagu-vps" -N ""
cat ~/.ssh/github_deploy_tsunagudake.pub
```
表示された1行を、GitHub のリポジトリの「Settings」→「Deploy keys」→「Add deploy key」に登録する（**「Allow write access」にチェックを入れない**）。

```bash
nano ~/.ssh/config
```
```
Host github-tsunagudake
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_deploy_tsunagudake
    IdentitiesOnly yes
```
```bash
ssh -T github-tsunagudake        # Hi …! You've successfully authenticated と出ればよい
sudo mkdir -p /var/www && sudo chown ubuntu:ubuntu /var/www
cd /var/www
git clone git@github-tsunagudake:TannaiTomoya/tsunagudake-app.git
cd tsunagudake-app
uv sync --frozen --no-dev        # .venv ができる（Python 3.14 も uv が入れる）
```

## 6. 設定（`.env`）
秘密の値を**画面に出さずに**書く形（実機で確認）。DB のパスワードは §4 で `~/.tsunagudake-db-password` に保存しておく（`umask 077 && openssl rand -hex 24 > ~/.tsunagudake-db-password`）。
```bash
cd /var/www/tsunagudake-app && umask 077 && cat > .env <<EOF
APP_ENV=demo
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL=mysql+pymysql://tsunagudake:$(cat ~/.tsunagudake-db-password)@127.0.0.1:3306/tsunagudake?charset=utf8mb4
BASE_URL=https://tsunagudake.example.jp
TRUSTED_HOSTS=tsunagudake.example.jp
PROXY_FIX=1
SESSION_COOKIE_SECURE=1
EOF
# デモの合言葉（12文字以上）。画面に出ない。入力を「英数」にしてから打つ
read -rs -p "合言葉: " P && echo && printf 'DEMO_PASSWORD=%s\n' "$P" >> .env && echo "（${#P}文字）" && unset P
umask 022                                              # 必ず戻す（注意12）
awk -F= '{print $1, "（" length($2) "文字）"}' .env   # 値は出さずに、長さだけ見る
```
手で書くときは、下のとおり。
```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"     # SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(12))"     # DEMO_PASSWORD
nano .env
chmod 600 .env
```
発表用（demo）で書くもの：

| 項目 | 値 |
|---|---|
| `APP_ENV` | `demo` |
| `SECRET_KEY` | 上で作った値 |
| `DATABASE_URL` | `mysql+pymysql://tsunagudake:パスワード@127.0.0.1:3306/tsunagudake?charset=utf8mb4` |
| `BASE_URL` | `https://tsunagudake.example.jp` |
| `TRUSTED_HOSTS` | `tsunagudake.example.jp` |
| `PROXY_FIX` | `1` |
| `SESSION_COOKIE_SECURE` | `1` |
| `DEMO_PASSWORD` | 上で作った値 |

- **`.env` は、人に見せない・Git に入れない・チャットや AI に貼らない。**
- 値は英数字だけにする。空白・日本語・`#`・引用符を入れない（読み込む道具によって、扱いが変わるため）。`=` の前後に空白を入れない。
- 足りない項目があると、アプリは理由を出して起動しない（02 §5.2）。

## 7. DB を作る
```bash
uv run --frozen --no-dev flask db upgrade     # 出力に MySQLImpl と出ること（SQLiteImpl なら DATABASE_URL が効いていない）
uv run --frozen --no-dev flask sync-municipalities   # 市町村の一覧から、無い行だけを足す（何度動かしても同じ）
uv run --frozen --no-dev flask seed-demo     # 発表用のデモ環境で架空のデータを入れる
# 手で起動して確かめる
uv run --frozen --no-dev --env-file .env gunicorn -c deploy/gunicorn.conf.py "app:create_app()"
# 別のターミナルで
curl -s -H "Host: tsunagudake.example.jp" http://127.0.0.1:8000/healthz    # ok
```
確かめたら、Ctrl + C で止める。

`deploy/gunicorn.conf.py` の中身：
```python
bind = "127.0.0.1:8000"          # 外には出さない。nginx だけが呼ぶ
workers = 2                      # メモリ 2GB の目安
timeout = 60
control_socket_disable = True    # 管理用の通信口は使わない
max_requests = 1000              # 長く動かしてもメモリが増え続けないよう、ときどき作り直す
max_requests_jitter = 100
accesslog = "-"                  # ログは systemd の journal へ
errorlog = "-"
```

## 8. 自動起動（systemd）
```bash
sudo cp deploy/tsunagudake.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/tsunagudake.service    # 何も出なければ、書き方は合っている
sudo systemctl daemon-reload
sudo systemctl enable --now tsunagudake
sudo systemctl status tsunagudake            # active (running)
```
`deploy/tsunagudake.service` の中身：
```ini
[Unit]
Description=tsunagudake-app (gunicorn)
After=network.target mysql.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/var/www/tsunagudake-app
EnvironmentFile=/var/www/tsunagudake-app/.env
ExecStart=/var/www/tsunagudake-app/.venv/bin/gunicorn -c deploy/gunicorn.conf.py "app:create_app()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
ログ：`sudo journalctl -u tsunagudake -n 50 --no-pager`。

### 8.1 守りを足す（動いてから、1行ずつ）
上の形で動くことを確かめてから、`[Service]` に1行ずつ足し、足すたびに `sudo systemctl daemon-reload && sudo systemctl restart tsunagudake` → `/healthz` を見る。動かなくなったら、その行を外す。
```ini
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
```
`ProtectHome=true` にはしない。uv が入れた Python は、ホームの下（`~/.local/share/uv/python/`）にあるので、ホームが見えないと起動しない。

## 9. nginx
```bash
sudo cp deploy/nginx-tsunagudake.conf /etc/nginx/sites-available/tsunagudake
sudo nano /etc/nginx/sites-available/tsunagudake     # server_name を自分のドメインに
sudo ln -s /etc/nginx/sites-available/tsunagudake /etc/nginx/sites-enabled/tsunagudake
sudo rm -f /etc/nginx/sites-enabled/default          # §10 の前に必ず外す（注意13）
sudo nginx -t && sudo systemctl reload nginx
ls /etc/nginx/sites-enabled/                         # tsunagudake だけになっていること
```
`/static/` が 403 になったら（注意12）、見た目のファイルだけを読めるようにする。`.env` には触らない。
```bash
cd /var/www/tsunagudake-app && sudo chmod 755 /var/www /var/www/tsunagudake-app app
find app/static -type d -exec chmod 755 {} + && find app/static -type f -exec chmod 644 {} +
namei -l app/static/css/theme.css                    # どの段で止まっているかを見る
```
`deploy/nginx-tsunagudake.conf` の中身（certbot の前の形）：
```nginx
server {
    listen 80;
    listen [::]:80;
    server_name tsunagudake.example.jp;

    server_tokens off;                 # nginx の版を出さない
    client_max_body_size 1m;           # アップロードが無い

    # 隠しファイル（.env・.git など）は、見せない
    location ~ /\.(?!well-known(?:/|$)) { deny all; }

    location /static/ {
        alias /var/www/tsunagudake-app/app/static/;
        expires 7d;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # 相手の IP を「上書き」して渡す（相手が付けてきた値を引き継がない）
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
        proxy_connect_timeout 5s;
    }
}
```

## 10. HTTPS
```bash
dig +short tsunagudake.example.jp      # 自分の IP が返ってから（返らなければ、まだ広まっていない。待つ）
sudo certbot --nginx -d tsunagudake.example.jp
sudo systemctl status certbot.timer    # 自動で更新される
```
**2つ以上の名前で開けるようにするとき**（例：引っ越しの間、新旧のドメインを両方開く）は、`server_name` に名前を並べてから、証明書を1枚にまとめる（注意14）。
```bash
sudo certbot --nginx --cert-name tsunagudake.example.jp -d tsunagudake.example.jp -d tsunagudake.old.example.jp
```
`.env` の `TRUSTED_HOSTS` も、カンマでつないで両方を書く。`BASE_URL` は、主にする方の1つだけ。

**ドメインを XServer で取ったとき**（実機で確認）：Xserverドメインの「ネームサーバー設定」で「XServer VPS クラウド」を選ぶ（`ns1〜3.vpscloud.ne.jp`）。VPS の「DNS設定」でドメインを追加し、A レコード（ホスト名は空・値は VPS の IP）を足す。`.jp` は、ネームサーバーを変えてから広まるまで数十分かかった。
ドメインが間に合わないとき：IP を含む名前（`203-0-113-10.sslip.io`）で証明書を取り、`BASE_URL` と `TRUSTED_HOSTS` をその名前にする。**http のままでは、demo も production も起動しない**（鍵マークなしで、ログインを通さない）。

## 11. 確かめる
- [ ] `https://tsunagudake.example.jp/healthz` が `ok`
- [ ] `http://…` で開くと、`https://…` に移る
- [ ] 全画面の上に「発表用のデモです…」が出る
- [ ] 「会計事務所として見る」「つなぎ手として見る」「自治体として見る」で入れる
- [ ] 自分のスマホ（Wi-Fi を切って）で、台本（01 §11）を最後まで通せる
- [ ] `sudo reboot` の後、何もしなくても開ける
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' -H "Host: evil.example" http://127.0.0.1:8000/healthz` が 400（サーバーの中から）
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' https://tsunagudake.example.jp/.env` が 403
- [ ] `sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '` が、2行とも `no`
- [ ] `.env` が `-rw-------`（`ls -l .env`）
- [ ] 一晩おいた翌朝も、最初の1回でエラーにならない（DB の接続が切れていても、つなぎ直す。02 §6）

## 12. 更新

変更はブランチで作り、PR を経由して `main` に入れます。`main` への push の検査が成功すると「VPS へ自動反映」が起動します。VPS の作業ツリーは `main` にしておきます。

### 12.1 自動反映の準備

1. VPS の `ubuntu` ユーザーに Actions 専用の SSH 公開鍵を登録します。VPS が GitHub を読むための Deploy key とは別の鍵です。
2. GitHub の `production` Environment に `VPS_DEPLOY_SSH_KEY`（Actions 専用の秘密鍵）と `VPS_DEPLOY_KNOWN_HOSTS`（VPS の照合済みホスト鍵）を Secrets として登録します。SSH ホスト鍵は VPS パネル等の信頼できる経路で照合します。
3. リポジトリの Actions Variables に `VPS_DEPLOY_HOST`（SSH のホスト名または IPv4）と `VPS_BASE_URL`（末尾スラッシュなしの公開 HTTPS URL）を登録します。
4. §13 の `my.cnf` を用意し、下記の手動反映を運用者が1回実行します。VPS 側にも検査済みコミットの引数を扱う版が入ります。
5. `sudo visudo -f /etc/sudoers.d/tsunagudake-deploy` で、サービスの再起動だけを許可します。

```sudoers
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart tsunagudake.service
```

6. 管理用のログインを確保したうえで、`sudo -l -U ubuntu` で既存のグループ設定も含めた権限を確認します。反映用アカウントの sudo 権限を上記の再起動に絞ってから、リポジトリの Actions Variable `VPS_DEPLOY_ENABLED` を `true` にします。

### 12.2 手動反映と結果の確認

```bash
cd /var/www/tsunagudake-app
bash deploy/deploy.sh https://tsunagudake.example.jp
```

更新前に DB の gzip を保存し（ここだけ `umask 077`）、`main` を対象コミットへ早送りして、`app/static` を読める権限に直し、`uv sync --frozen --no-dev`、`flask db upgrade`、`flask sync-municipalities`（市町村の無い行を足すだけ）、再起動を行います。公開 `/healthz` が HTTP 200 を返さなければ Actions に失敗が残ります。成功すると、古い反映の前の控えを整理します（§12.3）。自動実行では Actions 側と VPS 側で `main` を照合し、進んでいた場合は反映を見送ります。

更新用の控えは `~/.local/state/tsunagudake/backups/` に保存されます。変更は GitHub の PR で行い、VPS 上では手順どおりに取り込みます。

### 12.3 反映の前の控えの保存期間

**この節は、実機ではまだ確かめていません。**

反映が成功すると（公開 `/healthz` が 200）、`deploy/prune_deploy_backups.sh` が反映の前の控えを整理します。

- **14日より古い控えを消す。ただし、新しい順に3つは、古くても残す**（しばらく反映が無くても、戻すための控えを残すため）
- 消すのは `deploy.sh` が作る名前の形（`20261125T000000Z-xxxxxxxx.sql.gz`）のふつうのファイルだけ。ほかのファイルとリンクは消さない
- 控えの取得・移行・再起動・`/healthz` のどれかが失敗したときは、何も消さない
- 3つは日数の上限なしで残る。毎日の控え（§13。サーバーの中7日・外30日）とは別のフォルダ

**この版を入れる前に、今ある控えを確かめます。** この版が `main` に入った回の反映では、まだ古い `deploy.sh` が動くので消えません。**その次の反映から**、14日より古い控え（新しい順の3つを除く）が自動で消えます。残したいものがあれば、先に別の場所へ写します。

```bash
ls -l ~/.local/state/tsunagudake/backups/
```

反映を待たずに整理するときは、一覧を確かめてから、本人が実行します。

```bash
cd /var/www/tsunagudake-app && bash deploy/prune_deploy_backups.sh ~/.local/state/tsunagudake/backups
```

## 13. バックアップと復元の練習

毎日 **03:30（日本時間）** に `deploy/backup.sh` を実行します。時刻と一意の文字列を含む gzip をサーバー内に7日、別の会社のオブジェクトストレージに30日保存します。デプロイ前の控えとは別のディレクトリです。

### 13.1 鍵とバケットの準備

`ubuntu` のホームに専用ディレクトリを作り、次の2ファイルを権限 `600` で用意します。ストレージの鍵はバックアップ専用とし、対象バケットへの書き込みだけを許可します。復元時の読み取りには人のアカウントを使います。アプリの `.env` には入れません。

`~/.config/tsunagudake-backup/my.cnf`：

```ini
[client]
host=127.0.0.1
user=tsunagudake
password=ここにパスワード
```

`~/.config/tsunagudake-backup/s3.env`：

```ini
BACKUP_S3_ENDPOINT=https://storage.example.org
BACKUP_S3_BUCKET=バックアップ用バケット名
BACKUP_S3_REGION=提供元が指定するリージョン
BACKUP_S3_ACCESS_KEY_ID=バックアップ専用アクセスキーID
BACKUP_S3_SECRET_ACCESS_KEY=バックアップ専用シークレット
```

バケットを非公開にし、`tsunagudake/daily/` のオブジェクトが30日で期限切れになるライフサイクルを設定します。誤削除から復元できる設定も有効にし、管理画面で両方の設定を確認します。Google Cloud Storage では「削除（復元可能）」に相当します。

### 13.2 タイマー

```bash
sudo install -m 644 deploy/tsunagudake-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start tsunagudake-backup.service
sudo systemctl enable --now tsunagudake-backup.timer
systemctl list-timers tsunagudake-backup.timer
sudo journalctl -u tsunagudake-backup.service -n 20 --no-pager
```

初回は uv がロック済みのアップロード用依存を取得します。サーバー側の `~/.local/state/tsunagudake/daily-backups/` とバケットの両方に同名のファイルがあることを確認します。送信が失敗した場合はサービスが失敗として記録され、その回の gzip はサーバー内に残ります。

### 13.3 月1回の復元練習

読み取り権限のある人のアカウントでバケットから gzip を手元の PC にダウンロードします。控えを取得した時点の件数と比べてください。初回は書き込みのない時間帯か架空の DB で、バックアップ前に参照用の件数を控えます。

```bash
# 手元の隔離した MySQL（本番と同じ版）上で実行。復元先を新規作成し、既存なら中止します。
set -o pipefail
ARCHIVE=./tsunagudake-日時-識別子.sql.gz
gzip -t "$ARCHIVE" &&
  mysql --login-path=restore-local -e "CREATE DATABASE tsunagudake_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci" &&
  gunzip -c "$ARCHIVE" | mysql --login-path=restore-local tsunagudake_restore

Q="SELECT (SELECT COUNT(*) FROM users) AS users, (SELECT COUNT(*) FROM needs) AS needs, (SELECT COUNT(*) FROM ledger_entries) AS ledger, (SELECT version_num FROM alembic_version) AS revision;"
mysql --login-path=restore-local tsunagudake_restore -e "$Q"
```

`restore-local` は手元の MySQL を指すログインパスです。件数とマイグレーションの版が一致したら確認日を控え、ダウンロードした gzip は手元のアクセス制限された場所に保存します。

事故に気づいたら、先に既存の控えを別の場所へ退避します。本番への復元は、アプリを止め、復元直前の状態も保存したうえで、人が行います。

### 外部AIで相談の下書きを試す

事務所の「困りごとを書く」にある「AIで相談文の下書きを作る」から使います。本人がメモを入力して押すと、伏せ字にしたメモを Claude Messages API に送り、返事を編集欄に戻します。「下書きを保存して翻訳する」を押すまで相談は保存されません。

運営が試験利用を開始するときは、VPS のアプリ設定で `AI_DRIVER=claude`、利用できる `AI_MODEL`、`AI_API_KEY` を設定します。`AI_MONTHLY_LIMIT_YEN` は合意した試験予算（1,000円）、`AI_INPUT_YEN_PER_MTOK` と `AI_OUTPUT_YEN_PER_MTOK` は利用モデルの100万トークンあたりの円換算単価です。必要に応じて `AI_OFFICE_MONTHLY_LIMIT_YEN` で事務所別の上限を設定します。設定をサービスに反映したあと、架空の業務メモで下書きと手動保存を確認します。導入前と停止時は `AI_DRIVER=off` で相談を手入力できます。

### 通知メールの送信（段階16）

本番では認証付き SMTP リレーを用意し、送信元ドメインの認証を配信元の手順で済ませます。VPS の `ubuntu` ユーザーで `~/.config/tsunagudake-mail/smtp.env` を権限600で用意し、`SMTP_HOST`、`SMTP_PORT`（省略時465）、`SMTP_FROM`、`SMTP_USERNAME`、`SMTP_PASSWORD` を設定します。

```bash
sudo install -m 644 deploy/tsunagudake-mail.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tsunagudake-mail.timer
sudo journalctl -u tsunagudake-mail.service -n 20 --no-pager
```

送信結果は運営画面の「メールの記録」で確認できます。送信に失敗した記録は次回処理で再試行します。SMTP が受理した直後にワーカーが止まった場合は再送されることがあります。初回は管理下のテストアドレスで受信とリンクを確認します。運営画面のデモ用本文は、実際のメールとして送信されません。

### 台帳の毎日の検証と控え（段階22）

**この節は、実機ではまだ確かめていません。**

毎日 03:10（日本時間）に、監査記録の接続元の印（180日たったもの）を消し、境界線台帳の全部の鎖を検証して、鎖の最後のハッシュを控えます。控えのファイルは `~/.local/state/tsunagudake/ledger-anchors/` に、日付ごとに1つ出来ます（上書きしません）。03:30 のバックアップより前に動くので、その日の控えの行もバックアップに入ります。

```bash
sudo install -m 644 deploy/tsunagudake-daily.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start tsunagudake-daily.service
sudo systemctl enable --now tsunagudake-daily.timer
systemctl list-timers tsunagudake-daily.timer
sudo journalctl -u tsunagudake-daily.service -n 20 --no-pager
ls -l ~/.local/state/tsunagudake/ledger-anchors/
```

合わない記録があると、サービスは失敗として記録され、運営のアカウントにメールが出ます（メールの送信は上の「通知メールの送信」の設定が要ります）。運営の画面の「境界線台帳を検証する」にも、最後の控えが出ます。

- 控えのファイルは消さない・直さない。OS を入れ直すときは、このフォルダも控えてから移します（無いと、次の日に「前の控えのファイルがありません」と出ます）
- 合わないと出たら、先にその日のバックアップと控えのファイルを別の場所へ退避してから調べます

## 14. 発表の直前
```bash
cd /var/www/tsunagudake-app
uv run --frozen --no-dev flask reset-demo --yes     # 架空のデータを入れ直す
uv run --frozen --no-dev flask verify-ledger        # 台帳の鎖が合っていること
```
- 会場の回線が使えないときに備えて、手元の PC でも動く状態と、通しの録画を持っていく。
- 発表の相手に伝える：「データはすべて架空です。本物の情報は入力しないでください」。

## 15. やめるとき
- XServer アカウントで、契約の自動更新を止める（1か月契約なら、次の更新までに）。
- ドメインの A レコードを消す。GitHub の Deploy key を消す。
- バックアップのバケットと鍵を消す（鍵は、無効にしてから削除）。

## 16. OS を入れ直すとき

VPS パネルの「OS再インストール」で、契約はそのままに OS だけを入れ直せる。**サーバーの中のものは、すべて消える。**
架空のデータは `flask seed-demo` で作り直せる。次のものは作り直せないので、入れ直す前に読む。

| 消えるもの | 入れ直した後にすること |
|---|---|
| `.env`（`SECRET_KEY`・`DEMO_PASSWORD`・DB のパスワード） | §4・§6 で作り直す。デモ用のパスワードを人に伝えてあるなら、同じ値を入れ直す |
| サーバーのホスト鍵 | 自分の PC で `ssh-keygen -R 203.0.113.10`。GitHub の `VPS_DEPLOY_KNOWN_HOSTS` を入れ直す（§12.1） |
| `ubuntu` の `authorized_keys` | 自分の鍵（§2.2）と、Actions 専用の鍵（§12.1）を置き直す |
| GitHub から読むための鍵 | §5 で作り直す。GitHub の古い Deploy key は消す |
| `/etc/sudoers.d/tsunagudake-deploy`・systemd・nginx の設定 | §8・§9・§12.1 をやり直す |
| 証明書 | §10 で取り直す |
| `~/.config/tsunagudake-backup/`・`~/.config/tsunagudake-mail/` | §13 をやり直す。サーバーの中の控えも消える（バケットの控えは残る） |

順番：
1. GitHub の Actions Variable `VPS_DEPLOY_ENABLED` を `false` にする（入れ直しの途中で、自動反映を走らせない）。
2. VPS パネルで OS を入れ直す。SSH Key は、このときに登録できる（§1）。
3. IP アドレスが変わっていないかを、VPS パネルで見る。変わっていたら、A レコードと `VPS_DEPLOY_HOST` を直す。
4. §2 から §11 を、上から順に進める。
5. §12.1 をやり直し、`/healthz` が 200 になってから `VPS_DEPLOY_ENABLED` を `true` に戻す。

- 同じ名前の証明書は、Let's Encrypt では1週間に5枚まで。入れ直しを繰り返すときは、取った回数を数える。
- Ubuntu 26.04 で進めなくなったら、24.04 に入れ直して、同じ手順で進める。
- この節は、まだ実機で確かめていない。

## 17. torudake と同じサーバーに置くとき

torudake（Laravel）の入れ方は、torudake のリポジトリの手順書に書く。ここは、ぶつからないための注意だけ。**この節は、まだ実機で確かめていない。**

| 分けるもの | 注意 |
|---|---|
| nginx | 設定ファイルと `server_name` を、アプリごとに分ける。`certbot --nginx -d` も、ドメインごとに打つ |
| ポート | `127.0.0.1:8000` は、つなぐだけの gunicorn が使う。torudake は別の口（PHP-FPM のソケット）でつなぐ |
| MySQL | DB とユーザーを、アプリごとに作る。`tsunagudake` のユーザーは、`tsunagudake` の DB しか見えない（確かめた）。使い回さない |
| GitHub から読むための鍵 | 1つの鍵を、2つのリポジトリの Deploy key にはできない。鍵と、`~/.ssh/config` の別名を、アプリごとに作る |
| 置き場所・`.env` | `/var/www/` の下の別のフォルダ。`.env` は、それぞれ `chmod 600` |
| 再起動の許可 | sudoers は、サービスごとに1行 |
| バックアップ | `deploy/backup.sh` が取るのは、`tsunagudake` の DB だけ。torudake の DB は入らない |
| PHP | Ubuntu 26.04 の apt で入る PHP は 8.5。torudake が前提にしている版と違えば、torudake の側で確かめる |

メモリ：手元の 26.04 のコンテナでは、MySQL 8.4 が約520MB、つなぐだけの gunicorn が合わせて約200MB だった。2GB のプランに torudake を足したら、`free -m` の `available` を見る。

## 困ったとき
| 症状 | 見るところ |
|---|---|
| SSH で入れない（時間切れ） | パケットフィルターに「SSH」があるか。**VPS クラウドは、フィルターをサーバーの「ネットワーク」タブで適用したか**（注意11）。`ufw` で 22 を閉じていないか。VPS パネルの「コンソール」から入って直す |
| SSH がしばらくすると切れる | 自分の PC の `~/.ssh/config` に `ServerAliveInterval 60` を足す |
| https のトップが「Welcome to nginx!」 | `sites-enabled/default` が残っていて、証明書がそちらについた。外してから `certbot --nginx` で付け直す（注意13） |
| 画面に色や形がつかない（`/static/` が 403） | `umask 077` のまま取ってきた。§9 の直し方（注意12） |
| 片方の名前だけ証明書のエラー | 証明書が1つの名前だけに置き換わった。§10 の `--cert-name` で1枚にまとめる（注意14） |
| SSH が `REMOTE HOST IDENTIFICATION HAS CHANGED` で止まる | OS を入れ直して、ホスト鍵が変わった。`ssh-keygen -R 203.0.113.10`（§16） |
| パスワードでまだ入れてしまう | `sudo sshd -T | grep passwordauthentication`。`yes` なら、ほかの設定ファイルが勝っている（2.3） |
| `uv: command not found` | インストーラーが表示したコマンドを打っていない。SSH を入り直す |
| アプリが起動しない | `sudo journalctl -u tsunagudake -n 50 --no-pager`。足りない設定の名前が出る |
| 守りを足したら起動しない | 8.1 の最後に足した行を外す。`ProtectHome=true` になっていないか |
| 502 | `sudo systemctl status tsunagudake`。アプリが止まっている |
| 400 Bad Request | `TRUSTED_HOSTS` に、開いているドメインが入っていない |
| `flask db upgrade` が `SQLiteImpl` | `.env` の `DATABASE_URL` が効いていない（書き間違い・`=` の前後の空白） |
| `Access denied for user` | DB のパスワード。前後の空白 |
| `mysqldump` が `PROCESS privilege` で止まる | `--no-tablespaces` の付け忘れ |
| 日本語が `???` と出る | `mysql` に `--default-character-set=utf8mb4` を付ける（データは壊れていない） |
| `nginx -t` が `Address family not supported` | サーバーに IPv6 が無い。`listen [::]:80;` の行を消す |
| certbot が失敗する | A レコードがまだ広まっていない（`dig +short`）。80 番が、パケットフィルターと ufw の両方で開いているか |
| 朝いちばんだけエラーになる | DB の接続が切れている。`pool_pre_ping` と `pool_recycle` の設定（02 §6） |
| 全員がまとめてログインできなくなる | nginx が `X-Forwarded-For` を渡していない |
| スマホでだけ崩れる | 02 §8.5。表の列を減らす |

### PRを作り、CI成功後にマージする

ブランチをpushしたら、認証済みのGitHub CLIがある端末で `scripts/merge-after-ci.sh --create --title '変更の内容' --body-file PR説明.md` を実行します。説明に `Closes #番号` を書くと、マージ時に対応するIssueも閉じます。既に開いたPRや中断した待機は `scripts/merge-after-ci.sh PR番号` で再開できます。

コマンドはその端末で待機し、対象PRのSQLite・MySQL 8.0・8.4の検査が成功してからマージします。待機中にPRのコミットが変わった場合や、mainの変更を取り込む必要がある場合は停止します。マージ後は通常のmainのCIとVPS自動反映が続きます。
