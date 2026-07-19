# publish_news.py — パスマネ ニュース配信 一発スクリプト

ニュース1本を配信するために必要な3つの手作業を1コマンドに統合するスクリプトです。

1. `app/passmanager/news.html` に新しいお知らせブロックを追加（アプリのNewsタブはこれをWebView表示）
2. `news.html` を git commit & push（GitHub Pagesで自動配信）
3. Firebase Remote Config の `news_notification_badge` を更新（Newsタブの赤バッジ点灯用）
4. FCM（topic `news`）へプッシュ通知を送信（任意・省略可）

実行順は **1→2→3→4**（ニュースが読める状態になってから通知が飛ぶようにするため）です。

## 使い方

```bash
# 通常配信（本文はHTML断片として扱う。改行位置に<br>は自動挿入されない）
python3 tools/publish_news.py --title "アップデートのお知らせ" --body-file news_body.txt

# 日付を指定する（省略時は実行日）
python3 tools/publish_news.py --title "..." --body-file news_body.txt --date 2026/07/19

# 本文がプレーンテキストの場合（改行を自動で<br>に変換する）
python3 tools/publish_news.py --title "..." --body-file news_body.txt --plain-text

# 何も書き換えず、生成されるHTMLブロック・RC変更内容・FCMペイロードを確認するだけ
python3 tools/publish_news.py --title "..." --body-file news_body.txt --dry-run

# push / FCM送信を個別にスキップ
python3 tools/publish_news.py --title "..." --body-file news_body.txt --no-push
python3 tools/publish_news.py --title "..." --body-file news_body.txt --no-notify
```

`news_body.txt` は本文ファイルです。デフォルトではHTML断片としてそのまま挿入されるため、改行して見せたい箇所には自分で `<br>` を書いてください（既存の `news.html` の書き方と同じです）。`--plain-text` を付けると、ファイル内の改行がすべて `<br>` に変換されます（HTMLの特殊文字 `& < >` は自動エスケープされます）。

実行すると各ステップの成否が `[OK]` / `[NG]` / `[SKIP]` で表示されます。途中で失敗した場合は、そこまで完了したステップが表示されて停止します（後述の「途中で失敗した場合」を参照）。

`app/passmanager/news.html` の書き換え前には `/tmp/news.html.bak.<timestamp>` にバックアップが作成されます。

## 初回セットアップ

### 1. Python依存関係

標準ライブラリ以外に必要なのは `requests` のみです（RSA署名は後述のとおりsystemの `openssl` コマンドで行うため、`cryptography` 等のライブラリは不要です）。

```bash
pip3 install requests
```

（`externally-managed-environment` エラーが出る場合は `python3 -m venv .venv && source .venv/bin/activate && pip install requests` のように仮想環境を使うか、`pip3 install --user requests` を試してください。）

また、macOS/Linuxには標準搭載の `openssl` コマンドが必要です（FCM送信時のJWT署名に使用）。`which openssl` で確認できます。

### 2. firebase CLI（Remote Config更新に必要）

```bash
npm install -g firebase-tools
firebase login
```

ログインするアカウントは `passmanager-ba70f` プロジェクトの Remote Config を編集できる権限が必要です。

### 3. サービスアカウントJSON（FCM送信に必要・任意）

FCM送信を使わない場合（`--no-notify` を付ける、または未設定のまま実行する）はこの手順は不要です。未設定の場合、スクリプトは「未設定のためプッシュ送信をスキップしました」と表示してnews.html更新とRCバッジ更新だけを実行し、正常終了します。

FCM送信を使う場合:

1. [Firebaseコンソール](https://console.firebase.google.com/) → `passmanager-ba70f` プロジェクトを開く
2. 左上の歯車アイコン → **プロジェクトの設定**
3. **サービスアカウント** タブを開く
4. **新しい秘密鍵の生成** をクリックし、JSONファイルをダウンロード
5. ダウンロードしたファイルを `tools/service-account.json` として配置する
   - もしくは環境変数 `GOOGLE_APPLICATION_CREDENTIALS` に別の場所に置いたJSONファイルのパスを設定する（`tools/service-account.json` より優先されます）

`tools/service-account.json` は `.gitignore` に登録済みのため、誤ってコミットされることはありません。**このファイルは秘密情報です。第三者と共有しないでください。**

## FCMが実際にユーザーに届くための条件

このスクリプトはFCM HTTP v1 APIでtopic `news` 宛てに送信するだけです。実際にユーザーの端末に届くには、アプリ側で以下が完了している必要があります（現時点ではアプリ側のFCM統合は別途進行中です）。

- アプリがFCM SDKを統合し、起動時にtopic `news` を購読するビルドが配布済みであること
- [Firebase Console] → Project Settings → Cloud Messaging → Apple app configuration で **APNs認証キー（またはAPNs証明書）** が登録済みであること（登録されていないとiOS端末にはプッシュが届きません）

これらが未整備の間にこのスクリプトでFCM送信を実行しても、送信リクエスト自体は成功（200 OK）することがありますが、実機には届きません。

## 途中で失敗した場合

各ステップは順番に実行され、失敗した時点で停止し、それまでに完了したステップが表示されます。**同じコマンドをそのまま再実行すると、news.htmlの更新やcommitがすでに完了している場合に二重にブロックが追加されてしまうため注意してください。** 残りのステップだけを手動で行ってください。

### news.html更新・pushは済んだがRemote Config更新で失敗した場合

```bash
firebase remoteconfig:get --project passmanager-ba70f -o /tmp/rc.json
```

`/tmp/rc.json` を開き、`parameters.news_notification_badge.defaultValue.value` だけを新しい一意な値（例: `20260719-1830`）に書き換えて保存します。**他のパラメータは絶対に変更しないでください。**

```bash
cat > /tmp/rc_firebase.json <<'EOF'
{"remoteconfig": {"template": "rc.json"}}
EOF
cd /tmp && firebase deploy --only remoteconfig --project passmanager-ba70f --config /tmp/rc_firebase.json
```

### Remote Config更新までは済んだがFCM送信だけ失敗した場合

サービスアカウントJSONが設定済みであれば、次回同じ内容で `--no-push` を付けて実行すると、news.htmlへの二重追加はスキップしつつ再度Remote Config更新とFCM送信が走ってしまうため、FCM送信のみをやり直したい場合は個別に対応してください（このスクリプトには現時点でFCM送信のみを単独実行するオプションはありません）。必要であれば都度 `tools/publish_news.py` にオプションを追加するか、Firebaseコンソールの Cloud Messaging から手動送信してください。

## 検証時の注意（開発者向け）

このスクリプト自体を変更した際は、本物の `news.html` の書き換え・push・Remote Configへのdeploy・FCM送信を伴わずに検証してください。

- `--dry-run` は副作用がなく安全に実行できます（Remote Configの現在値取得のみ読み取り専用で行います）
- news.html書き換えロジック単体のテストは、`app/passmanager/news.html` を一時ディレクトリにコピーしたものに対して行い、`html.parser` でパースできることを確認してください
