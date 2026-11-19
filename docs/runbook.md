# 公開の手順（Xserver VPS） — つなぐだけ（Tsunagudake）

版1.1・2026-11-19。構成と理由は [02 技術仕様書 §11](02-technical-spec.md)。

**この手順は、本人が自分で打つ。** AI エージェントは本番に SSH しない。分からない所は、候補を出させて、読んでから自分で打つ。
前提：XServer VPS、Ubuntu 24.04、DB は VPS の中の MySQL、HTTPS、ドメインあり。
`example.jp`・`203.0.113.10` は、自分のものに読み替える。

---

## 確かめた記録（版1.1）

版1.0 は、1行も実行せずに書いていた。版1.1 で、**同じ Ubuntu 24.04 の上で実際に動かして**確かめ、見つかった不具合を直した。
「実機」と書いた所は、VPS の上でしか確かめられない。段階1（Issue #2）で、1行ずつ確かめて、違っていたらこの文書を直す。

| 節 | 何を | どう確かめたか | 結果 |
|---|---|---|---|
| 1 | 管理画面のメニューとボタンの名前 | XServer VPS の公式マニュアル | 文書で確認 |
| 1 | 2GB のプランを申し込めるか | — | **実機**（申し込みの画面） |
| 2 | SSH の設定ファイルの効き方 | Ubuntu 24.04・OpenSSH 9.6 で `sshd -T` | **不具合を直した**（下の注意1） |
| 2 | `adduser`・`ufw`・鍵でのログイン | — | **実機** |
| 3 | パッケージの名前 | Ubuntu 24.04 の apt で候補を確認。nginx 1.24.0・MySQL 8.0.46 は入れて動かした | 確認済み |
| 3 | uv のインストーラー | — | **実機** |
| 4 | DB とユーザーを作る SQL・待ち受けるアドレス・文字コード | MySQL 8.0.46 で実行 | 確認済み |
| 4 | アプリからの接続（URL の形・行のロック・CHECK 制約・長すぎる文字） | SQLAlchemy 2.1 ＋ PyMySQL で実行 | 確認済み |
| 7 | gunicorn の起動と設定ファイル | gunicorn 27.0 と最小の Flask アプリで実行 | 確認済み |
| 7 | `flask db upgrade`・`flask seed-demo` | — | **アプリができてから**（段階0・9） |
| 8 | systemd の設定の書き方 | systemd 255 の `systemd-analyze verify` | 書き方は確認済み。**起動は実機** |
| 9 | nginx の設定 | nginx 1.24.0 で `nginx -t`。nginx 越しに `/healthz` が 200、偽の IP が通らない、知らない Host が 400、隠しファイルが 403 | 確認済み（注意2・3） |
| 10 | 証明書（certbot）・ドメイン | — | **実機** |
| 11 | 再起動の後も動くか | — | **実機** |
| 13 | バックアップと復元 | `mysqldump` → `gzip -t` → 別の DB に戻す → 件数と、文字の中身（16進）が同じ | 確認済み（注意4） |
| 13 | サーバーの外へ送る所 | — | **実機**（段階10） |

見つけて直したこと：
1. **SSH の設定は、番号の小さいファイルが勝つ。** `99-…conf` に「パスワードでのログインを止める」と書いても、VPS に最初からある `50-cloud-init.conf` が「許す」と書いていると、**止まらない**（実際に確かめた）。ファイル名を `00-…conf` にし、`sshd -T` で効いていることを確かめる手順にした。
2. nginx は、相手の IP を `X-Forwarded-For` に**上書きして**渡す（相手が付けてきた値を引き継がない）。前の書き方でも、アプリ側の設定で偽の値は通らなかったが、アプリの設定に頼らない形にした。
3. IPv6 が無いサーバーでは、`listen [::]:80;` の行で nginx が起動しない（「困ったとき」に足した）。
4. `mysqldump` は `--no-tablespaces` を外すと、権限のエラーで止まる。日本語を画面で見るときは `--default-character-set=utf8mb4` を付ける（付けないと `???` と出ることがある。データは壊れていない）。
5. アプリの起動は、`uv run …` ではなく `.venv/bin/gunicorn` を直接呼ぶ形にした（起動のたびに uv を通さない）。
6. `dig` は最初から入っていないことがある（`dnsutils` を足した）。

---

## 0. 全体の流れ
1. VPS を申し込む　2. 最初の守り　3. 必要なものを入れる　4. MySQL　5. アプリを置く　6. 設定（`.env`）　7. DB を作る　8. 自動起動　9. nginx　10. HTTPS　11. 確かめる　12. 更新　13. バックアップ　14. 発表の直前　15. やめるとき

待ち時間があるのは、1（申し込み）と10（ドメインの向き先が広まるまで）。**ここを先にやる。**

## 1. VPS を申し込む
- 申し込むのは **「XServer VPS」**。名前の似ている「エックスサーバー（レンタルサーバー）」では、この構成は動かない。
- プラン：メモリ 2GB（申し込めなければ 4GB）。契約は**1か月**から。
- OS：Ubuntu 24.04。アプリのテンプレートは選ばない。
- root のパスワードは長いランダムなものにし、パスワード管理の道具に入れる。
- **SSH Key は、申し込みのときに登録する。** 公式マニュアルでは、設定できるのは「お申し込み時、またはOS再インストール時」。登録しなかった場合は、root のパスワードで入ってから、2の手順で鍵を置く。
- 控えるもの：IP アドレス（VPS パネルのトップページ）。
- ドメインの A レコードを、この IP に向ける（例：`tsunagudake.example.jp`）。

## 2. 最初の守り

### 2.1 パケットフィルター（VPS パネル）
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
nano /home/ubuntu/.ssh/authorized_keys     # 自分の PC の公開鍵（.pub）を1行貼る
chmod 600 /home/ubuntu/.ssh/authorized_keys
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
```

**別のターミナルを開き、`ssh ubuntu@203.0.113.10` で入れることと、`sudo -v` が通ることを確かめてから**、次に進む（確かめる前に閉じると、入れなくなる）。

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
```bash
# GitHub から読み取るための鍵（このサーバー専用）
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
uv run --frozen --no-dev flask seed-demo      # 架空のデータ
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
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
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
    location ~ /\.(?!well-known) { deny all; }

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
自動デプロイ（段階11）の前は、手で：
```bash
cd /var/www/tsunagudake-app
bash deploy/deploy.sh
```
`deploy/deploy.sh` がすること：①DB をバックアップ ②`git pull --ff-only` ③`uv sync --frozen --no-dev` ④`uv run --frozen --no-dev flask db upgrade` ⑤`sudo systemctl restart tsunagudake` ⑥`/healthz` が 200 を返すまで待つ（返らなければ、失敗として終わる）。
**サーバーの上でファイルを直さない。** 直すのは自分の PC で、push して、ここで取り込む。

## 13. バックアップ
設計は 02 §11.5。スクリプトは `deploy/backup.sh`（段階10）。
- 毎日 3:30（systemd timer）：`mysqldump` → サーバーの中（7日）→ 別の会社のオブジェクトストレージ（30日）
- 鍵は `~/.config/tsunagudake-backup/s3.env`（`chmod 600`）。アプリの `.env` と分ける。MySQL のパスワードは `~/.config/tsunagudake-backup/my.cnf`（`chmod 600`）に書き、`--defaults-extra-file` で渡す（**最初のオプションに書く**。パスワードをコマンドに直接書かない）

`~/.config/tsunagudake-backup/my.cnf`：
```
[client]
user=tsunagudake
password=ここにパスワード
```
取る：
```bash
CNF=$HOME/.config/tsunagudake-backup/my.cnf
STAMP=$(date +%Y%m%d-%H%M%S)        # 時刻まで入れる（同じ日に2回動かしても、上書きしない）
umask 077; mkdir -p ~/backup
mysqldump --defaults-extra-file=$CNF --single-transaction --no-tablespaces --default-character-set=utf8mb4 tsunagudake | gzip > ~/backup/tsunagudake-$STAMP.sql.gz
gzip -t ~/backup/tsunagudake-$STAMP.sql.gz && echo ok
```
**月1回、別の DB に戻して、件数を比べる：**
```bash
sudo mysql -e "CREATE DATABASE tsunagudake_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; GRANT ALL PRIVILEGES ON tsunagudake_restore.* TO 'tsunagudake'@'localhost';"
gunzip < ~/backup/tsunagudake-日付.sql.gz | mysql --defaults-extra-file=$CNF tsunagudake_restore
Q="SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM needs), (SELECT COUNT(*) FROM ledger_entries), (SELECT version_num FROM alembic_version);"
mysql --defaults-extra-file=$CNF tsunagudake -e "$Q"
mysql --defaults-extra-file=$CNF tsunagudake_restore -e "$Q"
sudo mysql -e "DROP DATABASE tsunagudake_restore;"
```
- 事故に気づいたら、**バックアップのスクリプトを動かさない。** 先に `cp -a ~/backup ~/backup-keep`。本番への復元は、アプリを止め、戻す前の状態も取ってから、人がする。

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

## 困ったとき
| 症状 | 見るところ |
|---|---|
| SSH で入れない | パケットフィルターに「SSH」があるか。`ufw` で 22 を閉じていないか。VPS パネルの「コンソール」から入って直す |
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
