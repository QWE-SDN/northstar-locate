"""Northstar Locate server with accounts, Whop checkout, and a small admin view.

The default SQLite database is suitable for local development. On Render, point
DATABASE_PATH at a persistent disk or replace it with a managed database before
accepting production traffic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
SESSION_TTL = 30 * 24 * 60 * 60
PASSWORD_ITERATIONS = 310_000
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")


def database_path() -> Path:
    configured = os.environ.get("DATABASE_PATH", "").strip()
    return Path(configured) if configured else ROOT / "data" / "northstar.sqlite3"


def db_connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with db_connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                subscription_plan TEXT,
                subscription_status TEXT NOT NULL DEFAULT 'inactive',
                whop_payment_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                plan TEXT,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
            """
        )


def now() -> int:
    return int(time.time())


def encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${encode_bytes(salt)}${encode_bytes(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), decode_bytes(salt_text), int(iterations)
        )
        return hmac.compare_digest(digest, decode_bytes(digest_text))
    except (ValueError, TypeError):
        return False


def safe_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "subscriptionPlan": row["subscription_plan"],
        "subscriptionStatus": row["subscription_status"],
    }


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    timestamp = now()
    with db_connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (timestamp,))
        db.execute(
            "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, timestamp + SESSION_TTL, timestamp),
        )
    return token


def cookie_value(handler: BaseHTTPRequestHandler, name: str) -> str:
    cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def user_from_request(handler: BaseHTTPRequestHandler) -> sqlite3.Row | None:
    token = cookie_value(handler, "northstar_session")
    if not token:
        return None
    with db_connect() as db:
        return db.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ?
            """,
            (token, now()),
        ).fetchone()


def cookie_header(name: str, value: str, max_age: int, secure: bool = True) -> str:
    flags = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
    return f"{flags}; Secure" if secure else flags


def request_is_secure(handler: BaseHTTPRequestHandler) -> bool:
    base = os.environ.get("PUBLIC_BASE_URL", "")
    return base.lower().startswith("https://") or handler.headers.get("X-Forwarded-Proto", "").lower() == "https"


def plan_config() -> dict[str, dict[str, Any]]:
    return {
        "monthly": {
            "planId": os.environ.get("WHOP_MONTHLY_PLAN_ID", "").strip(),
            "amount": 9900,
            "currency": "usd",
            "productName": "Northstar Locate - Monthly plan",
            "label": "$99.00 / 月",
        },
        "yearly": {
            "planId": os.environ.get("WHOP_YEARLY_PLAN_ID", "").strip(),
            "amount": 19900,
            "currency": "usd",
            "productName": "Northstar Locate - Yearly plan",
            "label": "$199.00 / 年",
        },
    }


def app_config() -> dict[str, Any]:
    return {"plans": plan_config()}


def checkout_signing_secret() -> str:
    return os.environ.get("WHOP_CHECKOUT_SIGNING_SECRET", "").strip()


def sign_checkout_token(user: sqlite3.Row, plan: str) -> str:
    secret = checkout_signing_secret()
    if not secret:
        raise RuntimeError("WHOP_CHECKOUT_SIGNING_SECRET is not configured")
    payload = {
        "userId": user["id"],
        "email": user["email"],
        "plan": plan,
        # Keep the signed metadata valid long enough for a delayed webhook retry.
        "expiresAt": now() + 24 * 60 * 60,
    }
    body = encode_bytes(json.dumps(payload, separators=(",", ":")).encode())
    signature = encode_bytes(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_checkout_token(token: str) -> dict[str, Any] | None:
    secret = checkout_signing_secret()
    try:
        body, signature = token.split(".", 1)
        expected = encode_bytes(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not secret or not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(decode_bytes(body).decode("utf-8"))
        if int(payload["expiresAt"]) < now() or payload["plan"] not in plan_config():
            return None
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def verify_whop_signature(payload: bytes, headers: Any) -> bool:
    secret = os.environ.get("WHOP_WEBHOOK_SECRET", "").strip()
    webhook_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signatures = headers.get("webhook-signature", "")
    if not secret or not webhook_id or not timestamp or not signatures:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
        # Whop signs with the complete ws_ secret as UTF-8 bytes.
        key = secret.encode("utf-8")
        signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + payload
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
        candidates = [item[3:] for item in signatures.split() if item.startswith("v1,")]
        return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
    except (ValueError, IndexError, TypeError):
        return False


def handle_whop_event(event: dict[str, Any]) -> None:
    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))
    data = event.get("data") or {}
    if not event_id:
        return
    with db_connect() as db:
        if db.execute("SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone():
            return
        db.execute(
            "INSERT INTO webhook_events(event_id, event_type, created_at) VALUES (?, ?, ?)",
            (event_id, event_type, now()),
        )
        if event_type not in {"payment.succeeded", "payment.failed", "payment.canceled"}:
            return
        payment_id = str(data.get("id", event_id))
        metadata = data.get("metadata") or {}
        token_payload = verify_checkout_token(str(metadata.get("northstar_checkout_token", "")))
        if not token_payload:
            print(f"Whop event {event_id} has no valid Northstar checkout token")
            return
        user = db.execute("SELECT * FROM users WHERE id = ?", (int(token_payload["userId"]),)).fetchone()
        if user is None or user["email"] != token_payload["email"]:
            print(f"Whop event {event_id} references an unknown account")
            return
        status = {
            "payment.succeeded": "active",
            "payment.failed": "payment_failed",
            "payment.canceled": "canceled",
        }[event_type]
        db.execute(
            """
            INSERT OR REPLACE INTO payments(payment_id, user_id, plan, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (payment_id, user["id"], token_payload["plan"], status, json.dumps(data), now()),
        )
        db.execute(
            """
            UPDATE users SET subscription_plan = ?, subscription_status = ?,
                whop_payment_id = ?, updated_at = ? WHERE id = ?
            """,
            (token_payload["plan"], status, payment_id, now(), user["id"]),
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "NorthstarLocate/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: int, payload: dict[str, Any], cookies: list[str] | None = None) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_text(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def require_user(self) -> sqlite3.Row | None:
        return user_from_request(self)

    def admin_ok(self) -> bool:
        password = os.environ.get("ADMIN_PASSWORD", "")
        token = cookie_value(self, "northstar_admin")
        if not password or not token:
            return False
        try:
            timestamp_text, signature = decode_bytes(token).decode().split(".", 1)
            timestamp = int(timestamp_text)
            expected = hmac.new(password.encode(), timestamp_text.encode(), hashlib.sha256).hexdigest()
            return now() - timestamp < SESSION_TTL and hmac.compare_digest(expected, signature)
        except (ValueError, TypeError, UnicodeDecodeError):
            return False

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json(HTTPStatus.OK, app_config())
            return
        if parsed.path == "/api/auth/me":
            user = self.require_user()
            self.send_json(HTTPStatus.OK, {"authenticated": user is not None, "user": safe_user(user)})
            return
        if parsed.path == "/api/admin/users":
            if not self.admin_ok():
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "admin authentication required"})
                return
            with db_connect() as db:
                rows = db.execute(
                    """
                    SELECT email, subscription_plan, subscription_status, whop_payment_id,
                           created_at, updated_at FROM users ORDER BY created_at DESC
                    """
                ).fetchall()
            users = [
                {
                    "email": row["email"],
                    "subscriptionPlan": row["subscription_plan"],
                    "subscriptionStatus": row["subscription_status"],
                    "whopPaymentId": row["whop_payment_id"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in rows
            ]
            self.send_json(HTTPStatus.OK, {"users": users})
            return

        relative = parsed.path.lstrip("/") or "index.html"
        if relative == "admin":
            relative = "admin.html"
        candidate = (PUBLIC_DIR / relative).resolve()
        if PUBLIC_DIR not in candidate.parents and candidate != PUBLIC_DIR:
            self.send_text(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
            return
        if not candidate.is_file():
            self.send_text(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        self.send_text(HTTPStatus.OK, candidate.read_bytes(), content_types.get(candidate.suffix.lower(), "application/octet-stream"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/webhook", "/api/webhooks/whop"}:
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if not verify_whop_signature(payload, self.headers):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid Whop webhook signature"})
                return
            try:
                handle_whop_event(json.loads(payload.decode("utf-8")))
            except (json.JSONDecodeError, TypeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self.send_json(HTTPStatus.OK, {"received": True})
            return
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if parsed.path == "/api/auth/register":
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            if not EMAIL_RE.fullmatch(email):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请输入有效邮箱地址。"})
                return
            if len(password) < 8:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "密码至少需要 8 位。"})
                return
            timestamp = now()
            try:
                with db_connect() as db:
                    cursor = db.execute(
                        "INSERT INTO users(email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (email, hash_password(password), timestamp, timestamp),
                    )
                    user_id = cursor.lastrowid
                    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            except sqlite3.IntegrityError:
                self.send_json(HTTPStatus.CONFLICT, {"error": "该邮箱已经注册，请直接登录。"})
                return
            token = create_session(int(user_id))
            self.send_json(HTTPStatus.OK, {"user": safe_user(user)}, [cookie_header("northstar_session", token, SESSION_TTL, request_is_secure(self))])
            return

        if parsed.path == "/api/auth/login":
            email = str(body.get("email", "")).strip().lower()
            password = str(body.get("password", ""))
            with db_connect() as db:
                user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user is None or not verify_password(password, user["password_hash"]):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "邮箱或密码不正确。"})
                return
            token = create_session(user["id"])
            self.send_json(HTTPStatus.OK, {"user": safe_user(user)}, [cookie_header("northstar_session", token, SESSION_TTL, request_is_secure(self))])
            return

        if parsed.path == "/api/auth/logout":
            token = cookie_value(self, "northstar_session")
            if token:
                with db_connect() as db:
                    db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.send_json(HTTPStatus.OK, {"ok": True}, [cookie_header("northstar_session", "", 0, request_is_secure(self))])
            return

        if parsed.path == "/api/whop-checkout-token":
            user = self.require_user()
            if user is None:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
                return
            plan = str(body.get("planId", "")).strip()
            settings = plan_config().get(plan)
            if not settings or not settings["planId"]:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "该 Whop 套餐尚未配置。"})
                return
            try:
                token = sign_checkout_token(user, plan)
            except RuntimeError as exc:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
                return
            self.send_json(HTTPStatus.OK, {"planId": settings["planId"], "token": token, "email": user["email"]})
            return

        if parsed.path == "/api/admin/login":
            password = str(body.get("password", ""))
            configured = os.environ.get("ADMIN_PASSWORD", "")
            if not configured:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "ADMIN_PASSWORD 未配置。"})
                return
            if not hmac.compare_digest(password, configured):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "后台密码不正确。"})
                return
            timestamp_text = str(now())
            signature = hmac.new(configured.encode(), timestamp_text.encode(), hashlib.sha256).hexdigest()
            token = encode_bytes(f"{timestamp_text}.{signature}".encode())
            self.send_json(HTTPStatus.OK, {"ok": True}, [cookie_header("northstar_admin", token, SESSION_TTL, request_is_secure(self))])
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "4243"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Northstar Locate site listening at http://{host}:{port}")
    print("Account registration, Whop checkout, and admin endpoints are ready.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
