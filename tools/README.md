# publish_news.py — パスマネ ニュース配信 一発スクリプト

`app/passmanager/news-content.json` を正データとして、ニュース配信に必要な作業を1コマンドに統合するスクリプトです。

## 新フロー（JSON正データ方式）

1. **`app/passmanager/news-content.json` にニュースを追記する**（手編集、または後述の `--add` オプション）
2. **`python3 tools/publish_news.py` を実行する**
   - `news-content.json` の内容から `app/passmanager/news.html` を全再生成する（既存のHTML構造・cssクラスを踏襲した静的生成。アプリのNewsタブや旧バージョンのアプリはこのnews.htmlをWebView表示している）
   - `news-content.json` と `news.html` を git commit & push（GitHub Pagesで自動配信）
   - Firebase Remote Config の `news_notification_badge` を更新（新JSON方式に対応していない旧バージョンのアプリ向けのバッジ用に引き続き使用）
3. **Firebase Console からプッシュ通知を手動送信する**（後述）

`news.html` はもう手編集しません。`news-content.json` を編集して、このスクリプトで再生成してください。

## news-content.json のスキーマ

```json
{
  "version": 2026071901,
  "items": [
    {
      "id": "2026-07-19-update",
      "date": "2026/07/19",
      "title": "アップデートを行いました",
      "body": "本文。改行は\n。HTMLタグは使わない",
      "links": [{ "label": "ダウンロードはこちら", "url": "https://..." }]
    }
  ]
}
```

- `items` は新しい順（先頭が最新）。新しいニュースは配列の**先頭**に追加してください。
- `version` は日付連番（`YYYYMMDDNN`）。ニュースを追加・変更したら値を大きくしてください（`--add` を使えば自動で連番になります）。
- `body` はプレーンテキストのみ。改行はJSON文字列内の `\n` で表現し、HTMLタグは書かないでください。
- リンクは `links` 配列にまとめて入れます（本文中の特定の位置に埋め込むことはできません。生成される `news.html` では本文の後ろにリンク一覧として表示されます）。

## 使い方

### (A) news-content.json を手編集した場合

```bash
# 通常配信（news.html再生成 → push → RCバッジ更新）
python3 tools/publish_news.py

# 何も書き換えず、生成されるnews.htmlの差分サマリ・git commitメッセージ・RC変更内容を確認するだけ
python3 tools/publish_news.py --dry-run

# push だけスキップしたい場合
python3 tools/publish_news.py --no-push
```

### (B) `--add` で新しいニュースを追記する場合

`news-content.json` の先頭にitemを挿入し、versionを自動インクリメントしたうえで、そのまま (A) と同じ配信処理を実行します。

```bash
python3 tools/publish_news.py --add --title "アップデートのお知らせ" --body-file news_body.txt

# 日付を指定する（省略時は実行日）
python3 tools/publish_news.py --add --title "..." --body-file news_body.txt --date 2026/07/19

# リンクを付ける（"ラベル|URL" 形式。複数回指定可）
python3 tools/publish_news.py --add --title "..." --body-file news_body.txt \
  --link "ダウンロードはこちら|https://apps.apple.com/jp/app/id6449425671"

# 内容を確認するだけ（何も書き換えない）
python3 tools/publish_news.py --add --title "..." --body-file news_body.txt --dry-run
```

`news_body.txt` はプレーンテキストのファイルです（HTMLタグは書かないでください）。改行はそのまま `news-content.json` の `body` に反映されます。

実行すると各ステップの成否が `[OK]` / `[NG]` / `[SKIP]` で表示されます。途中で失敗した場合は、そこまで完了したステップが表示されて停止します。`news.html` の書き換え前には `/tmp/news.html.bak.<timestamp>` にバックアップが作成されます。

## プッシュ通知の送信（Firebase Console から手動）

**このスクリプトはプッシュ通知（FCM）を送信しません。** ニュースを配信したらユーザーに知らせたい場合は、スクリプト実行後に表示される案内に従って手動で送信してください。

1. [Firebase Console](https://console.firebase.google.com/project/passmanager-ba70f/messaging) → **Messaging** を開く
2. **新しいキャンペーン** → **Firebase通知メッセージ** を選択
3. 配信対象を **トピック** → `news` に設定して送信

送信履歴・開封率などもFirebase Console上で確認できます。

### 前提: APNs認証キーの登録

iOS端末にプッシュが届くには、[Firebase Console] → プロジェクトの設定 → **Cloud Messaging** → Apple app configuration で **APNs認証キー（またはAPNs証明書）** が登録済みである必要があります。未登録の場合、Firebase Console からの送信自体は成功しても実機には届きません。

## 旧 publish_news.py からの変更点

| | 旧 | 新 |
|---|---|---|
| 正データ | `news.html` を直接手編集・スクリプトで断片挿入 | `news-content.json`。`news.html` は毎回全再生成される |
| news.html更新 | 新規ブロックをHTML断片として先頭に挿入 | JSON全体から静的生成（手編集不要） |
| プッシュ通知(FCM) | スクリプトが `topic: news` へ直接送信（サービスアカウントJSON・opensslによるJWT署名が必要） | **廃止**。Firebase Console から手動送信する運用に変更 |
| 追加の依存関係 | `requests`、system の `openssl`、（任意で）`tools/service-account.json` | なし（標準ライブラリのみ。`firebase` CLI は引き続き必要） |
| RCバッジ更新 | ○（変更なし） | ○（変更なし。`news_notification_badge` のみ更新、他パラメータは触らない） |
| 新規追記の補助 | `--title`/`--body-file` でHTML断片を直接組み立て | `--add` で `news-content.json` にitemを挿入（`--link` でリンクを複数追加可） |

`tools/service-account.json`（FCM送信用の秘密鍵）はもう使用しません。既存のファイルが残っていても問題ありませんが、不要であれば削除して構いません。

## 初回セットアップ

### firebase CLI（Remote Config更新に必要）

```bash
npm install -g firebase-tools
firebase login
```

ログインするアカウントは `passmanager-ba70f` プロジェクトの Remote Config を編集できる権限が必要です。

Python側の追加の依存関係はありません（標準ライブラリのみで動作します）。

## 途中で失敗した場合

各ステップは順番に実行され、失敗した時点で停止し、それまでに完了したステップが表示されます。**同じコマンドをそのまま再実行すると、`--add` で挿入したitemやcommitがすでに完了している場合に二重に追加されてしまうため注意してください。** 残りのステップだけを手動で行ってください。

### news.html再生成・pushは済んだがRemote Config更新で失敗した場合

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

## 検証時の注意（開発者向け）

このスクリプト自体を変更した際は、本物の `news-content.json`／`news.html` の書き換え・push・Remote Configへのdeployを伴わずに検証してください。

- `--dry-run` は副作用がなく安全に実行できます（Remote Configの現在値取得のみ読み取り専用で行います）
- `news-content.json` が `python3 -m json.tool app/passmanager/news-content.json` でパースできることを確認してください
- `render_full_html()` が生成する `news.html` と現在のファイルとの差分は `--dry-run` の出力（先頭40行）で確認できます。全文で確認したい場合は `python3 -c "import sys; sys.path.insert(0,'tools'); import publish_news as p; print(p.render_full_html(p.load_news_content()))"` のように直接関数を呼び出してください（ファイルへの書き込みは行われません）
