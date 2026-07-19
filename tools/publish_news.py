#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
パスマネ ニュース配信 一発スクリプト（JSON正データ方式）

app/passmanager/news-content.json を正データとして扱い、ニュース配信に必要な作業を1コマンドに統合する。

  1. news-content.json の内容から app/passmanager/news.html を全再生成する
     （旧バージョンのアプリ・古いキャッシュ向けに news.html 自体も引き続き配信する。
       手編集はもう不要。既存のHTML構造・cssクラスは踏襲した静的生成）
  2. news-content.json と news.html を git commit & push（GitHub Pagesで自動配信）
  3. Firebase Remote Config の news_notification_badge を更新
     （新JSON方式に対応していない旧バージョンのアプリ向けに残しているNewsタブの赤バッジ用。
       このパラメータ以外は絶対に変更しない）

実行順は 1→2→3（ニュースが読める状態になってから通知バッジが立つようにするため）。

プッシュ通知（FCM）の送信はこのスクリプトの役割ではなくなった。ニュースをユーザーに知らせたい場合は
実行後の案内に従い、Firebase Console → Messaging から手動で送信すること（送信履歴もそこで確認できる）。

使い方:
    # (A) news-content.json を手編集した後、それを配信する
    python3 tools/publish_news.py
    python3 tools/publish_news.py --dry-run

    # (B) 新しいニュースの追記を1コマンドで行う（先頭にitem挿入 → versionインクリメント → そのまま配信）
    python3 tools/publish_news.py --add --title "アップデートのお知らせ" --body-file news_body.txt
    python3 tools/publish_news.py --add --title "..." --body-file body.txt --date 2026/07/19
    python3 tools/publish_news.py --add --title "..." --body-file body.txt --link "ダウンロードはこちら|https://apps.apple.com/..."
    python3 tools/publish_news.py --add --title "..." --body-file body.txt --dry-run
    python3 tools/publish_news.py --add --title "..." --body-file body.txt --no-push

必要なもの:
    - python3 標準ライブラリのみ（外部パッケージ不要）
    - firebase CLI（認証済み。Remote Config 更新に使用）

詳細は tools/README.md を参照。
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import html
import json
import shutil
import subprocess
import sys
import tempfile
import os
from pathlib import Path

# ----------------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
NEWS_HTML_PATH = REPO_ROOT / "app" / "passmanager" / "news.html"
NEWS_CONTENT_PATH = REPO_ROOT / "app" / "passmanager" / "news-content.json"

FIREBASE_PROJECT = "passmanager-ba70f"
RC_PARAM_NAME = "news_notification_badge"

# news.html のヘッダ・フッタ（枠部分）のテンプレート。
# 現行の news.html から抽出したもの（<head>のメタ情報、<body>開始〜
# <div class="contents description"> まで）をそのまま踏襲している。
# フッタ側は元のnews.htmlに閉じタグが無かった分を補って明示的に閉じている
# （ブラウザは元々暗黙的に閉じて解釈していたため見た目には影響しない）。
HEADER_TEMPLATE = """<!DOCTYPE HTML>
<html lang="ja">

<head>
    <meta charset="utf-8">
    <title>お得カレンダー　ValueCalendar</title>
    <meta name="viewport" content="width=device-width">
    <meta name="keywords" content="カレンダー">
    <meta name="description" content="お得カレンダー　ValueCalendar">
    <link rel="stylesheet" type="text/css" href="http://yui.yahooapis.com/3.16.0-rc-1/build/cssreset/cssreset-min.css">
    <link rel="stylesheet" href="css/common.css" type="text/css" media="all">
    <!--[if lt IE 9]>
    <script src="http://html5shim.googlecode.com/svn/trunk/html5.js"></script>
    <![endif]-->
</head>

<body id="news">
    <div class="wrap">
        <div class="contents description">
"""

FOOTER_TEMPLATE = """        </div>
    </div>
</body>

</html>
"""

BLOCK_INDENT = " " * 12
DESC_INDENT = " " * 16
LEFT_INDENT = " " * 20
BODY_INDENT = " " * 24

# 新JSONスキーマに category フィールドは存在しないため、contentsLeft（左上のラベル）は
# 固定文言「お知らせ」で生成する。旧news.htmlには「注意喚起」「お願い」「ご案内」など
# ブロックごとに異なるラベルが手打ちされていたケースがあるが、今後は統一する。
FIXED_CATEGORY_LABEL = "お知らせ"


# ----------------------------------------------------------------------------
# news-content.json の読み書き
# ----------------------------------------------------------------------------

def load_news_content() -> dict:
    if not NEWS_CONTENT_PATH.is_file():
        raise SystemExit(f"エラー: {NEWS_CONTENT_PATH} が見つかりません")
    with open(NEWS_CONTENT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if "version" not in data or "items" not in data:
        raise SystemExit(f"エラー: {NEWS_CONTENT_PATH} のスキーマが不正です（version/itemsが必要）")
    return data


def save_news_content(data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    NEWS_CONTENT_PATH.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------
# news.html 生成（news-content.json → HTML）
# ----------------------------------------------------------------------------

def render_body_html(body: str, links: list[dict]) -> str:
    """本文プレーンテキスト(\\n区切り) + links[] から <p class="middleText"> の中身を生成する。

    本文は行ごとに <br> を付与してそのまま表示する。links はリンク集として
    本文の後ろにまとめて表示する（新スキーマは本文中の挿入位置を持たないため）。
    """
    lines = body.split("\n") if body else []
    rendered_lines = [
        f"{BODY_INDENT}{html.escape(line, quote=False)}<br>" if line else f"{BODY_INDENT}<br>"
        for line in lines
    ]

    if links:
        if rendered_lines:
            rendered_lines.append(f"{BODY_INDENT}<br>")
        for link in links:
            label = html.escape(link["label"], quote=True)
            url = html.escape(link["url"], quote=True)
            rendered_lines.append(f'{BODY_INDENT}<a href="{url}" target="_blank">{label}</a><br>')

    return "\n".join(rendered_lines)


def render_item_html(item: dict) -> str:
    """1件のニュースitemを既存の contentsBorder ブロックと同じ構造のHTMLにする。"""
    title = html.escape(item["title"], quote=False)
    date_display = html.escape(item["date"], quote=False)
    body_html = render_body_html(item.get("body", ""), item.get("links", []))

    return (
        f"{BLOCK_INDENT}<!-- ここからボーダーの１ブロック始まり -->\n"
        f'{BLOCK_INDENT}<div class="contentsBorder">\n'
        f'{DESC_INDENT}<div class="contentsDescription">\n'
        f'{LEFT_INDENT}<div class="contentsLeft">\n'
        f"{BODY_INDENT}<p>{FIXED_CATEGORY_LABEL}</p>\n"
        f"{LEFT_INDENT}</div>\n"
        f'{LEFT_INDENT}<div class="contentsCenter">&nbsp</div>\n'
        f'{LEFT_INDENT}<div class="contentsRight">\n'
        f"{BODY_INDENT}<p>{date_display}</p>\n"
        f"{LEFT_INDENT}</div>\n"
        f"{DESC_INDENT}</div>\n"
        f"\n"
        f"{DESC_INDENT}<div>\n"
        f"{LEFT_INDENT}<h2>{title}</h2>\n"
        f'{LEFT_INDENT}<p class="middleText"><br>\n'
        f"{body_html}\n"
        f"{LEFT_INDENT}</p>\n"
        f"{DESC_INDENT}</div>\n"
        f"{BLOCK_INDENT}</div>\n"
    )


def render_full_html(data: dict) -> str:
    blocks = "\n".join(render_item_html(item) for item in data["items"])
    return HEADER_TEMPLATE + blocks + "\n" + FOOTER_TEMPLATE


def write_news_html(html_text: str) -> Path | None:
    """news.html を書き換える。書き換え前に /tmp にバックアップを作成する。"""
    backup_path = None
    if NEWS_HTML_PATH.is_file():
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = Path(tempfile.gettempdir()) / f"news.html.bak.{ts}"
        shutil.copy2(NEWS_HTML_PATH, backup_path)
    NEWS_HTML_PATH.write_text(html_text, encoding="utf-8")
    return backup_path


# ----------------------------------------------------------------------------
# --add モード: news-content.json の先頭に新規itemを挿入
# ----------------------------------------------------------------------------

def parse_date_arg(date_str: str | None) -> str:
    """--date 引数をパースし、表示用 'YYYY/MM/DD' を返す（省略時は今日の日付）。"""
    if date_str is None:
        d = datetime.date.today()
    else:
        try:
            d = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        except ValueError:
            raise SystemExit(
                f'エラー: --date は "YYYY/MM/DD" 形式で指定してください（例: 2026/07/19）: {date_str!r}'
            )
    return d.strftime("%Y/%m/%d")


def parse_link_arg(raw: str) -> dict:
    if "|" not in raw:
        raise SystemExit(f'エラー: --link は "ラベル|URL" の形式で指定してください: {raw!r}')
    label, url = raw.split("|", 1)
    label, url = label.strip(), url.strip()
    if not label or not url:
        raise SystemExit(f'エラー: --link のラベルとURLは両方とも空にできません: {raw!r}')
    return {"label": label, "url": url}


def make_unique_id(existing_ids: set[str], date_display: str) -> str:
    m = date_display.replace("/", "-")
    parts = m.split("-")
    if len(parts) == 3:
        base_id = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    else:
        base_id = m
    if base_id not in existing_ids:
        return base_id
    n = 2
    while f"{base_id}-{n}" in existing_ids:
        n += 1
    return f"{base_id}-{n}"


def compute_next_version(current_version: int) -> int:
    """version は日付連番(YYYYMMDDNN)。同じ日ならNNをインクリメント、日付が変わっていれば01から。"""
    today_prefix = int(datetime.date.today().strftime("%Y%m%d"))
    current_prefix = current_version // 100
    if current_prefix == today_prefix:
        seq = current_version % 100
        return today_prefix * 100 + min(seq + 1, 99)
    return today_prefix * 100 + 1


def build_new_item(args: argparse.Namespace, data: dict) -> dict:
    body_file_path = Path(args.body_file)
    if not body_file_path.is_file():
        raise SystemExit(f"エラー: 本文ファイルが見つかりません: {body_file_path}")
    body = body_file_path.read_text(encoding="utf-8").rstrip("\n")

    date_display = parse_date_arg(args.date)
    links = [parse_link_arg(raw) for raw in (args.link or [])]
    existing_ids = {item["id"] for item in data["items"]}
    item_id = make_unique_id(existing_ids, date_display)

    return {
        "id": item_id,
        "date": date_display,
        "title": args.title,
        "body": body,
        "links": links,
    }


# ----------------------------------------------------------------------------
# 共通ヘルパー
# ----------------------------------------------------------------------------

def run(cmd: list[str], cwd: str | None = None) -> str:
    """サブプロセスを実行し、失敗時は分かりやすいメッセージで RuntimeError を送出する。"""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(f"コマンドが見つかりません: {cmd[0]}（{e}）") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"コマンド失敗: {' '.join(cmd)}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout


def generate_badge_value() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M")


# ----------------------------------------------------------------------------
# Firebase Remote Config 更新
# ----------------------------------------------------------------------------

def update_remote_config_badge(new_value: str) -> str | None:
    """news_notification_badge の defaultValue.value のみを書き換えてデプロイする。

    それ以外のパラメータ・条件は取得したテンプレートのまま変更しない。
    戻り値は変更前の値（取得できなければ None）。
    """
    with tempfile.TemporaryDirectory(prefix="publish_news_rc_") as tmpdir:
        template_path = os.path.join(tmpdir, "remoteconfig.template.json")
        firebase_json_path = os.path.join(tmpdir, "firebase.json")

        run(["firebase", "remoteconfig:get", "--project", FIREBASE_PROJECT, "-o", template_path])

        with open(template_path, encoding="utf-8") as f:
            template = json.load(f)

        param = template.get("parameters", {}).get(RC_PARAM_NAME)
        if param is None:
            raise RuntimeError(f'Remote Config に "{RC_PARAM_NAME}" パラメータが見つかりませんでした')

        old_value = param.get("defaultValue", {}).get("value")
        param.setdefault("defaultValue", {})["value"] = new_value

        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)

        with open(firebase_json_path, "w", encoding="utf-8") as f:
            json.dump({"remoteconfig": {"template": "remoteconfig.template.json"}}, f)

        run(
            [
                "firebase",
                "deploy",
                "--only",
                "remoteconfig",
                "--project",
                FIREBASE_PROJECT,
                "--config",
                firebase_json_path,
            ],
            cwd=tmpdir,
        )

    return old_value


def fetch_current_badge_value() -> str | None:
    """dry-run のプレビュー用に現在値だけを読み取る（読み取り専用・副作用なし）。"""
    with tempfile.TemporaryDirectory(prefix="publish_news_rc_preview_") as tmpdir:
        template_path = os.path.join(tmpdir, "remoteconfig.template.json")
        run(["firebase", "remoteconfig:get", "--project", FIREBASE_PROJECT, "-o", template_path])
        with open(template_path, encoding="utf-8") as f:
            template = json.load(f)
    param = template.get("parameters", {}).get(RC_PARAM_NAME, {})
    return param.get("defaultValue", {}).get("value")


PUSH_REMINDER = (
    "\n"
    "==============================================================\n"
    "プッシュ通知はこのスクリプトの対象外です。ユーザーに知らせたい場合は\n"
    "Firebase Console → Messaging → 新しいキャンペーン → トピック \"news\" 宛てに\n"
    "プッシュを送信してください（送信履歴もそこで確認できます）。\n"
    "https://console.firebase.google.com/project/" + FIREBASE_PROJECT + "/messaging\n"
    "==============================================================\n"
)


# ----------------------------------------------------------------------------
# メイン処理
# ----------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "パスマネ ニュース配信を1コマンドで実行する"
            "（news-content.json → news.html再生成 → push → RCバッジ更新）"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--add",
        action="store_true",
        help="news-content.json の先頭に新しいニュースを追記してから配信する",
    )
    parser.add_argument("--title", default=None, help="[--add用] ニュースのタイトル")
    parser.add_argument("--body-file", default=None, help="[--add用] 本文ファイルのパス（プレーンテキスト）")
    parser.add_argument("--date", default=None, help='[--add用] 表示日付。省略時は今日の日付（"YYYY/MM/DD"形式）')
    parser.add_argument(
        "--link",
        action="append",
        help='[--add用] "ラベル|URL" 形式のリンク。複数回指定可（例: --link "ダウンロードはこちら|https://..."）',
    )
    parser.add_argument("--no-push", action="store_true", help="git commit & push をスキップする")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="何も書き換えず、生成されるnews.htmlの差分サマリ・git commitメッセージ・RC変更内容を表示するだけ",
    )
    args = parser.parse_args(argv)

    if args.add:
        if not args.title or not args.body_file:
            parser.error("--add には --title と --body-file が必須です")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    data = load_news_content()
    added_item = None

    if args.add:
        added_item = build_new_item(args, data)
        data["items"].insert(0, added_item)
        data["version"] = compute_next_version(data["version"])

    new_html = render_full_html(data)

    if added_item is not None:
        commit_message = f"news: {added_item['title']} ({added_item['date']})"
    else:
        commit_message = f"news: news-content.json を反映 (version {data['version']})"

    badge_value = generate_badge_value()

    if args.dry_run:
        print("=" * 60)
        if added_item is not None:
            print("[dry-run] news-content.json に追記されるitem:")
            print(json.dumps(added_item, ensure_ascii=False, indent=2))
            print("=" * 60)
            print("[dry-run] 生成されるHTMLブロック:")
            print(render_item_html(added_item), end="")
            print("=" * 60)
        print(f"[dry-run] 反映後の version: {data['version']}")
        print(f"[dry-run] items件数: {len(data['items'])}")
        if NEWS_HTML_PATH.is_file():
            old_html = NEWS_HTML_PATH.read_text(encoding="utf-8")
            diff_lines = list(
                difflib.unified_diff(
                    old_html.splitlines(keepends=True),
                    new_html.splitlines(keepends=True),
                    fromfile="news.html (現在)",
                    tofile="news.html (生成後)",
                )
            )
            if diff_lines:
                print(f"[dry-run] news.html との差分: {len(diff_lines)} 行変更あり（先頭40行を表示）")
                print("".join(diff_lines[:40]))
            else:
                print("[dry-run] news.html との差分なし")
        print("=" * 60)
        print(f"[dry-run] git commit メッセージ: {commit_message}")
        print("=" * 60)
        print(f"[dry-run] Remote Config 変更内容: {RC_PARAM_NAME}")
        try:
            current_value = fetch_current_badge_value()
            print(f"  現在値: {current_value!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  現在値: 取得できませんでした（{e}）")
        print(f"  新しい値: {badge_value!r}")
        print("=" * 60)
        print("[dry-run] 何も書き換えていません。")
        print(PUSH_REMINDER)
        return 0

    completed_steps: list[str] = []

    if added_item is not None:
        save_news_content(data)
        completed_steps.append("news-content.json更新")
        print(f"[OK] news-content.json に「{added_item['title']}」を追記しました（version {data['version']}）")

    backup_path = write_news_html(new_html)
    completed_steps.append("news.html再生成")
    if backup_path:
        print(f"[OK] news.html を再生成しました（バックアップ: {backup_path}）")
    else:
        print("[OK] news.html を再生成しました")

    if args.no_push:
        print("[SKIP] --no-push のため git commit/push をスキップしました")
    else:
        try:
            run(["git", "-C", str(REPO_ROOT), "add", str(NEWS_CONTENT_PATH), str(NEWS_HTML_PATH)])
            run(["git", "-C", str(REPO_ROOT), "commit", "-m", commit_message])
            run(["git", "-C", str(REPO_ROOT), "push", "origin", "master"])
        except RuntimeError as e:
            print(f"[NG] git commit/push に失敗しました: {e}")
            print(f"ここまで完了: {', '.join(completed_steps)}")
            return 1
        completed_steps.append("git push")
        print("[OK] git commit & push が完了しました（GitHub Pagesで配信されます）")

    try:
        old_badge_value = update_remote_config_badge(badge_value)
    except RuntimeError as e:
        print(f"[NG] Remote Config の更新に失敗しました: {e}")
        print(f"ここまで完了: {', '.join(completed_steps)}")
        return 1
    completed_steps.append("Remote Configバッジ更新")
    print(f"[OK] Remote Config {RC_PARAM_NAME} を更新しました（{old_badge_value!r} → {badge_value!r}）")

    print("すべての処理が完了しました。")
    print(PUSH_REMINDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
