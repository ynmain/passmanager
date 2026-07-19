#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
パスマネ ニュース配信 一発スクリプト

ニュース1本を配信するために必要な3つの手作業を1コマンドに統合する。
  1. app/passmanager/news.html に新しいお知らせブロックを追加
  2. news.html を git commit & push（GitHub Pagesで自動配信）
  3. Firebase Remote Config の news_notification_badge を更新（Newsタブの赤バッジ点灯用）
  4. FCM（topic "news"）へプッシュ通知を送信（任意・省略可）

実行順は 1→2→3→4（ニュースが読める状態になってから通知が飛ぶようにするため）。

使い方:
    python3 tools/publish_news.py --title "アップデートのお知らせ" --body-file news_body.txt
    python3 tools/publish_news.py --title "..." --body-file body.txt --date 2026/07/19
    python3 tools/publish_news.py --title "..." --body-file body.txt --plain-text
    python3 tools/publish_news.py --title "..." --body-file body.txt --dry-run
    python3 tools/publish_news.py --title "..." --body-file body.txt --no-push --no-notify

必要なもの:
    - python3 標準ライブラリ + requests（`pip3 install requests` が必要な場合あり）
    - system の openssl コマンド（FCM送信時のJWT署名に使用。macOS/Linuxには標準搭載）
    - firebase CLI（認証済み。news.html更新・pushとは別に Remote Config 更新に使用）
    - （任意）tools/service-account.json または環境変数 GOOGLE_APPLICATION_CREDENTIALS
      （FCM送信に使用。未設定でもnews.html更新とRCバッジ更新は実行される）

詳細は tools/README.md を参照。
"""

from __future__ import annotations

import argparse
import base64
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "エラー: requests が見つかりません。`pip3 install requests` を実行してください。\n"
        "詳細は tools/README.md を参照してください。\n"
    )
    sys.exit(1)


# ----------------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
NEWS_HTML_PATH = REPO_ROOT / "app" / "passmanager" / "news.html"

FIREBASE_PROJECT = "passmanager-ba70f"
RC_PARAM_NAME = "news_notification_badge"

FCM_TOPIC = "news"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

SERVICE_ACCOUNT_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
SERVICE_ACCOUNT_DEFAULT_PATH = REPO_ROOT / "tools" / "service-account.json"

FIRST_BLOCK_COMMENT = "<!-- ここからボーダーの１ブロック始まり -->"
FIRST_BLOCK_DIV = '<div class="contentsBorder">'

BODY_INDENT = " " * 24


# ----------------------------------------------------------------------------
# news.html ブロック生成・挿入
# ----------------------------------------------------------------------------

def format_body(body_text: str, plain_text: bool) -> str:
    """本文を news.html のマークアップに合わせて整形する。

    plain_text=True の場合は HTMLエスケープした上で改行を <br> に変換する。
    plain_text=False（デフォルト）の場合は本文をHTML断片としてそのまま扱う
    （改行の位置に <br> は自動挿入しない。呼び出し側で <br> を含めておくこと）。
    """
    text = body_text.rstrip("\n")
    if plain_text:
        text = html.escape(text, quote=False)
        text = text.replace("\n", "<br>\n")

    lines = text.split("\n")
    indented = [BODY_INDENT + line if line.strip() else "" for line in lines]
    return "\n".join(indented)


def build_new_block(title: str, date_display: str, body_text: str, plain_text: bool) -> str:
    """既存の news.html の contentsBorder ブロックと同じ構造の新規ブロックを生成する。"""
    escaped_title = html.escape(title, quote=False)
    body_html = format_body(body_text, plain_text)

    return (
        f"            {FIRST_BLOCK_COMMENT}\n"
        f"            {FIRST_BLOCK_DIV}\n"
        f'                <div class="contentsDescription">\n'
        f'                    <div class="contentsLeft">\n'
        f"                        <p>お知らせ</p>\n"
        f"                    </div>\n"
        f'                    <div class="contentsCenter">&nbsp</div>\n'
        f'                    <div class="contentsRight">\n'
        f"                        <p>{date_display}</p>\n"
        f"                    </div>\n"
        f"                </div>\n"
        f"\n"
        f"                <div>\n"
        f"                    <h2>{escaped_title}</h2>\n"
        f'                    <p class="middleText"><br>\n'
        f"{body_html}\n"
        f"                    </p>\n"
        f"                </div>\n"
        f"            </div>\n"
    )


def insert_block_into_html(news_html_text: str, new_block: str) -> str:
    """最初の <div class="contentsBorder"> ブロックの直前に new_block を挿入する。

    直前に "ここからボーダーの１ブロック始まり" コメントがあれば、
    そのコメントの前（＝新ブロックとしてコメント込みで独立させる位置）に挿入する。
    """
    lines = news_html_text.splitlines(keepends=True)

    div_idx = None
    for i, line in enumerate(lines):
        if line.strip() == FIRST_BLOCK_DIV:
            div_idx = i
            break

    if div_idx is None:
        raise RuntimeError(f'news.html 内に "{FIRST_BLOCK_DIV}" が見つかりませんでした')

    insert_idx = div_idx
    if div_idx > 0 and lines[div_idx - 1].strip() == FIRST_BLOCK_COMMENT:
        insert_idx = div_idx - 1

    return "".join(lines[:insert_idx]) + new_block + "".join(lines[insert_idx:])


def make_backup(path: Path) -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(tempfile.gettempdir()) / f"news.html.bak.{ts}"
    shutil.copy2(path, backup_path)
    return backup_path


def write_updated_news_html(path: Path, new_block: str) -> None:
    original = path.read_text(encoding="utf-8")
    updated = insert_block_into_html(original, new_block)
    path.write_text(updated, encoding="utf-8")


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


def parse_date_arg(date_str: str | None) -> tuple[str, str]:
    """--date 引数をパースし、(表示用 'YYYY/MM/DD', ISO 'YYYY-MM-DD') を返す。"""
    if date_str is None:
        d = datetime.date.today()
    else:
        try:
            d = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        except ValueError:
            raise SystemExit(
                f'エラー: --date は "YYYY/MM/DD" 形式で指定してください（例: 2026/07/19）: {date_str!r}'
            )
    return d.strftime("%Y/%m/%d"), d.isoformat()


def generate_badge_value() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M")


def strip_html_for_notification(body_html: str, limit: int = 180) -> str:
    """プッシュ通知本文用にHTML断片からプレーンテキストを抽出する。"""
    text = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


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


# ----------------------------------------------------------------------------
# FCM 送信
# ----------------------------------------------------------------------------

def resolve_service_account_path() -> Path | None:
    env_path = os.environ.get(SERVICE_ACCOUNT_ENV)
    if env_path:
        p = Path(env_path)
        return p if p.is_file() else None
    return SERVICE_ACCOUNT_DEFAULT_PATH if SERVICE_ACCOUNT_DEFAULT_PATH.is_file() else None


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def get_access_token(service_account_path: Path) -> tuple[str, str]:
    """サービスアカウントJSONから手動でJWTを組み立て、OAuth2アクセストークンを取得する。

    RSA-SHA256署名はsystemの openssl コマンドで行う（外部Pythonライブラリ不要）。
    戻り値は (access_token, project_id)。
    """
    with open(service_account_path, encoding="utf-8") as f:
        sa = json.load(f)

    client_email = sa["client_email"]
    private_key_pem = sa["private_key"]
    project_id = sa.get("project_id", FIREBASE_PROJECT)
    token_uri = sa.get("token_uri", "https://oauth2.googleapis.com/token")

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": FCM_SCOPE,
        "aud": token_uri,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _base64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )

    with tempfile.TemporaryDirectory(prefix="publish_news_jwt_") as tmpdir:
        key_path = os.path.join(tmpdir, "key.pem")
        data_path = os.path.join(tmpdir, "signing_input.bin")
        sig_path = os.path.join(tmpdir, "signature.bin")

        with open(key_path, "w", encoding="utf-8") as f:
            f.write(private_key_pem)
        os.chmod(key_path, 0o600)

        with open(data_path, "wb") as f:
            f.write(signing_input.encode("ascii"))

        try:
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_path, "-out", sig_path, data_path],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "openssl コマンドが見つかりません。FCM送信にはsystemのopensslが必要です。"
            ) from e

        if result.returncode != 0:
            raise RuntimeError(f"JWT署名(openssl)に失敗しました: {result.stderr}")

        with open(sig_path, "rb") as f:
            signature = f.read()

    jwt_token = signing_input + "." + _base64url_encode(signature)

    resp = requests.post(
        token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth2トークン取得に失敗しました: {resp.status_code} {resp.text}")

    access_token = resp.json().get("access_token")
    if not access_token:
        raise RuntimeError(f"OAuth2レスポンスに access_token がありません: {resp.text}")

    return access_token, project_id


def build_fcm_payload(title: str, body: str) -> dict:
    return {
        "message": {
            "topic": FCM_TOPIC,
            "notification": {
                "title": title,
                "body": body,
            },
        }
    }


def send_fcm(access_token: str, project_id: str, payload: dict) -> dict:
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"FCM送信に失敗しました: {resp.status_code} {resp.text}")
    return resp.json()


# ----------------------------------------------------------------------------
# メイン処理
# ----------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="パスマネ ニュース配信を1コマンドで実行する（news.html更新 → push → RCバッジ更新 → FCM送信）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--title", required=True, help="ニュースのタイトル（h2に入る）")
    parser.add_argument("--body-file", required=True, help="本文ファイルのパス（HTML断片可）")
    parser.add_argument("--date", default=None, help='表示日付。省略時は今日の日付（"YYYY/MM/DD"形式）')
    parser.add_argument(
        "--plain-text",
        action="store_true",
        help="本文をプレーンテキストとして扱い、改行を<br>に変換する（デフォルトはHTML断片として扱う）",
    )
    parser.add_argument("--no-push", action="store_true", help="git commit & push をスキップする")
    parser.add_argument("--no-notify", action="store_true", help="FCM送信をスキップする")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="何も書き換えず、生成されるHTMLブロック・RC変更内容・FCMペイロードを表示するだけ",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    body_file_path = Path(args.body_file)
    if not body_file_path.is_file():
        print(f"[NG] 本文ファイルが見つかりません: {body_file_path}")
        return 1
    body_text = body_file_path.read_text(encoding="utf-8")

    date_display, date_iso = parse_date_arg(args.date)
    new_block = build_new_block(args.title, date_display, body_text, args.plain_text)
    commit_message = f"news: {args.title} ({date_iso})"
    badge_value = generate_badge_value()
    # プッシュ通知本文はプレーンテキストのみ対応のため、HTML断片・プレーンテキストの
    # どちらの入力でも <br> や他のタグを除去してプレビュー用のテキストに変換する。
    notification_body = strip_html_for_notification(body_text)
    fcm_payload = build_fcm_payload(args.title, notification_body)

    if args.dry_run:
        print("=" * 60)
        print("[dry-run] 生成される news.html ブロック:")
        print("=" * 60)
        print(new_block, end="")
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
        if args.no_notify:
            print("[dry-run] FCM送信: --no-notify のためスキップされます")
        else:
            sa_path = resolve_service_account_path()
            print("[dry-run] FCM送信ペイロード (topic: news):")
            print(json.dumps(fcm_payload, ensure_ascii=False, indent=2))
            if sa_path is None:
                print(
                    "  ※ サービスアカウントJSON未設定のため、実行時はFCM送信がスキップされます"
                    "（README参照）"
                )
            else:
                print(f"  ※ サービスアカウント: {sa_path}")
        print("=" * 60)
        print("[dry-run] 何も書き換えていません。")
        return 0

    completed_steps: list[str] = []

    # バックアップ
    backup_path = make_backup(NEWS_HTML_PATH)
    print(f"[OK] news.html バックアップ: {backup_path}")

    # 1. news.html 更新
    try:
        write_updated_news_html(NEWS_HTML_PATH, new_block)
    except Exception as e:  # noqa: BLE001
        print(f"[NG] news.html の更新に失敗しました: {e}")
        print(f"バックアップから復元できます: cp {backup_path} {NEWS_HTML_PATH}")
        return 1
    completed_steps.append("news.html更新")
    print("[OK] news.html を更新しました")

    # 2. git commit & push
    if args.no_push:
        print("[SKIP] --no-push のため git commit/push をスキップしました")
    else:
        try:
            run(["git", "-C", str(REPO_ROOT), "add", "app/passmanager/news.html"])
            run(["git", "-C", str(REPO_ROOT), "commit", "-m", commit_message])
            run(["git", "-C", str(REPO_ROOT), "push", "origin", "master"])
        except RuntimeError as e:
            print(f"[NG] git commit/push に失敗しました: {e}")
            print(f"ここまで完了: {', '.join(completed_steps)}")
            return 1
        completed_steps.append("git push")
        print("[OK] git commit & push が完了しました（GitHub Pagesで配信されます）")

    # 3. Remote Config バッジ更新
    try:
        old_badge_value = update_remote_config_badge(badge_value)
    except RuntimeError as e:
        print(f"[NG] Remote Config の更新に失敗しました: {e}")
        print(f"ここまで完了: {', '.join(completed_steps)}")
        return 1
    completed_steps.append("Remote Configバッジ更新")
    print(f"[OK] Remote Config {RC_PARAM_NAME} を更新しました（{old_badge_value!r} → {badge_value!r}）")

    # 4. FCM送信
    if args.no_notify:
        print("[SKIP] --no-notify のため FCM送信をスキップしました")
    else:
        sa_path = resolve_service_account_path()
        if sa_path is None:
            print("未設定のためプッシュ送信をスキップしました。設定方法はREADME参照")
        else:
            try:
                access_token, project_id = get_access_token(sa_path)
                send_fcm(access_token, project_id, fcm_payload)
            except RuntimeError as e:
                print(f"[NG] FCM送信に失敗しました: {e}")
                print(f"ここまで完了: {', '.join(completed_steps)}")
                return 1
            completed_steps.append("FCM送信")
            print(f"[OK] FCM送信が完了しました（topic: {FCM_TOPIC}）")

    print("すべての処理が完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
