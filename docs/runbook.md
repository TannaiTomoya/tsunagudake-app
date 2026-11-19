# 公開の手順（Xserver VPS） — つなぐだけ（Tsunagudake）

版1.0・2026-11-19。構成と理由は [02 技術仕様書 §11](02-technical-spec.md)。

**この手順は、本人が自分で打つ。** AI エージェントは本番に SSH しない。分からない所は、候補を出させて、読んでから自分で打つ。
前提：Xserver VPS、Ubuntu 24.04、DB は VPS の中の MySQL、HTTPS、ドメインあり。
`example.jp`・`203.0.113.10`・`あなたのユーザー名` は、自分のものに読み替える。
管理画面の項目名は変わることがある（【画面で確かめる】と書いた所）。

---

## 0. 全体の流れ
1. VPS を申し込む　2. 最初の守り　3. 必要なものを入れる　4. MySQL　5. アプリを置く　6. 設定（`.env`）　7. DB を作る　8. 自動起動　9. nginx　10. HTTPS　11. 確かめる　12. 更新　13. バックアップ　14. 発表の直前　15. やめるとき

待ち時間があるのは、1（申し込み）と10（ドメインの向き先が広まるまで）。**ここを先にやる。**

## 1. VPS を申し込む
- 申し込むのは **「XServer VPS」**。名前の似ている「エックスサーバー（レンタルサーバー）」では、この構成は動かない。
- プラン：メモリ 2GB（申し込めなければ 4GB）。契約は**1か月**から。【画面で確かめる】
- OS：Ubuntu 24.04。アプリのテンプレートは選ばない。
- root のパスワードは長いランダムなものにし、パスワード管理の道具に入れる。SSH の鍵を登録できる画面なら、ここで登録する。
- 控えるもの：IP アドレス。
- ドメインの A レコードを、この IP に向ける（例：`tsunagudake.example.jp`）。

## 2. 最初の守り
契約の直後は、パケットフィルターがすべて閉じている。VPS の管理画面の「パケットフィルター設定」で、**SSH** と **Web（80・443）** だけを足す。【画面で確かめる】

```bash
# 自分の PC から（最初だけ root）
ssh root@203.0.113.10

apt update && apt -y upgrade
timedatectl set-timezone Asia/Tokyo

# ふだん使うユーザー（キャンプの教材と同じ名前にすると、教材のコマンドがそのまま使える）
adduser ubuntu
usermod -aG sudo ubuntu
mkdir -p /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh
nano /home/ubuntu/.ssh/authorized_keys     # 自分の PC の公開鍵（.pub）を1行貼る
chmod 600 /home/ubuntu/.ssh/authorized_keys
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
```

**別のターミナルを開き、`ssh ubuntu@203.0.113.10` で入れることを確かめてから**、次に進む（確かめる前に閉じると、入れなくなる）。

```bash
# ubuntu で入ってから。root でのログインと、パスワードでのログインを止める
sudo nano /etc/ssh/sshd_config.d/99-tsunagudake.conf
```
```
PermitRootLogin no
PasswordAuthentication no
```
```bash
sudo sshd -t && sudo systemctl restart ssh

# サーバーの中のファイアウォール
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
sudo apt -y install nginx mysql-server git curl certbot python3-certbot-nginx
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc && uv --version
```

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
mysql -u tsunagudake -p tsunagudake -e "SELECT CURRENT_USER();"   # tsunagudake@localhost と出ればよい
sudo ss -ltnp | grep 3306                                        # 127.0.0.1 だけで待ち受けていること
```
MySQL は外に開けない（3306 番は、ufw でもパケットフィルターでも開けない）。

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
uv sync --frozen --no-dev
```

## 6. 設定（`.env`）
```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(18))" # DEMO_PASSWORD
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

**`.env` は、人に見せない・Git に入れない・チャットや AI に貼らない。** 足りない項目があると、アプリは理由を出して起動しない（02 §5.2）。

## 7. DB を作る
```bash
uv run --frozen --no-dev flask db upgrade     # 出力に MySQLImpl と出ること（SQLiteImpl なら DATABASE_URL が効いていない）
uv run --frozen --no-dev flask seed-demo      # 架空のデータ
# 手で起動して確かめる
uv run --frozen --no-dev --env-file .env gunicorn "app:create_app()" --bind 127.0.0.1:8000 --workers 1
# 別のターミナルで
curl -s -H "Host: tsunagudake.example.jp" http://127.0.0.1:8000/healthz    # ok
```
確かめたら、Ctrl + C で止める。

## 8. 自動起動（systemd）
```bash
sudo cp deploy/tsunagudake.service /etc/systemd/system/
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
User=ubuntu
Group=ubuntu
WorkingDirectory=/var/www/tsunagudake-app
EnvironmentFile=/var/www/tsunagudake-app/.env
ExecStart=/home/ubuntu/.local/bin/uv run --frozen --no-dev gunicorn "app:create_app()" --bind 127.0.0.1:8000 --workers 2 --timeout 60
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
ログ：`sudo journalctl -u tsunagudake -n 50 --no-pager`。

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
    client_max_body_size 1m;

    location /static/ {
        alias /var/www/tsunagudake-app/app/static/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
`X-Forwarded-For` を渡さないと、回数制限が全員をまとめて止めてしまう。

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
- [ ] `curl -I -H "Host: evil.example" https://tsunagudake.example.jp/` が 400
- [ ] `.env` が `-rw-------`（`ls -l .env`）

## 12. 更新
自動デプロイ（段階11）の前は、手で：
```bash
cd /var/www/tsunagudake-app
bash deploy/deploy.sh      # バックアップ → git pull → uv sync → flask db upgrade → 再起動 → /healthz を待つ
```
**サーバーの上でファイルを直さない。** 直すのは自分の PC で、push して、ここで取り込む。

## 13. バックアップ
設計は 02 §11.5。スクリプトは `deploy/backup.sh`（段階10）。
- 毎日 3:30（systemd timer）：`mysqldump` → サーバーの中（7日）→ 別の会社のオブジェクトストレージ（30日）
- 鍵は `~/.config/tsunagudake-backup/s3.env`（`chmod 600`）。アプリの `.env` と分ける。MySQL のパスワードは `~/.config/tsunagudake-backup/my.cnf`（`--defaults-extra-file` で渡す。最初のオプションに書く）
- **月1回、別の DB に戻して、件数を比べる。**
```bash
sudo mysql -e "CREATE DATABASE tsunagudake_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; GRANT ALL PRIVILEGES ON tsunagudake_restore.* TO 'tsunagudake'@'localhost';"
gunzip < ~/backup/tsunagudake-日付.sql.gz | mysql --defaults-extra-file=$HOME/.config/tsunagudake-backup/my.cnf tsunagudake_restore
# users・needs・ledger_entries の件数を、本番と比べる
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
- Xserver の管理画面で、契約の自動更新を止める（1か月契約なら、次の更新までに）。
- ドメインの A レコードを消す。GitHub の Deploy key を消す。
- バックアップのバケットと鍵を消す（鍵は、無効にしてから削除）。

## 困ったとき
| 症状 | 見るところ |
|---|---|
| SSH で入れない | パケットフィルターに SSH があるか。`ufw` で 22 を閉じていないか。VPS の管理画面の「コンソール」から入って直す |
| アプリが起動しない | `sudo journalctl -u tsunagudake -n 50 --no-pager`。足りない設定の名前が出る |
| 502 | `sudo systemctl status tsunagudake`。アプリが止まっている |
| 400 Bad Request | `TRUSTED_HOSTS` に、開いているドメインが入っていない |
| `flask db upgrade` が `SQLiteImpl` | `.env` の `DATABASE_URL` が効いていない（書き間違い・`=` の前後の空白） |
| `Access denied for user` | DB のパスワード。前後の空白 |
| certbot が失敗する | A レコードがまだ広まっていない（`dig +short`）。80 番が開いているか |
| 全員がまとめてログインできなくなる | nginx が `X-Forwarded-For` を渡していない |
| スマホでだけ崩れる | 02 §8.5。表の列を減らす |
