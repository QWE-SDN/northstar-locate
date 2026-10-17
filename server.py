"""Small, dependency-light Stripe Payment Element server.

Stripe secrets stay on the server. The browser receives only the publishable
key and a short-lived PaymentIntent client secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
STRIPE_API = "https://api.stripe.com/v1"


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting real environment variables."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(ROOT / ".env")


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def config() -> dict[str, Any]:
    currency = os.environ.get("STRIPE_CURRENCY", "usd").lower()
    if not re.fullmatch(r"[a-z]{3}", currency):
        currency = "usd"

    amount = env_int("STRIPE_AMOUNT", 1999)
    product_name = os.environ.get("STRIPE_PRODUCT_NAME", "Example digital product")
    return {
        "publishableKey": os.environ.get("STRIPE_PUBLISHABLE_KEY", ""),
        "amount": amount,
        "currency": currency,
        "productName": product_name,
        "priceId": os.environ.get("STRIPE_PRICE_ID", "").strip(),
        "plans": {
            "single": {
                "amount": env_int("STRIPE_SINGLE_AMOUNT", 90),
                "currency": currency,
                "productName": os.environ.get(
                    "STRIPE_SINGLE_PRODUCT_NAME", "Northstar Locate - Single request"
                ),
            },
            "monthly": {
                "amount": env_int("STRIPE_MONTHLY_AMOUNT", 1990),
                "currency": currency,
                "productName": os.environ.get(
                    "STRIPE_MONTHLY_PRODUCT_NAME", "Northstar Locate - Monthly plan"
                ),
            },
        },
    }


class StripeAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def stripe_request(
    method: str,
    path: str,
    params: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret:
        raise StripeAPIError(500, "STRIPE_SECRET_KEY is not configured on the server")

    token = base64.b64encode(f"{secret}:".encode("utf-8")).decode("ascii")
    body = None
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "stripe-checkout-elements-demo/1.0",
    }
    if params is not None:
        body = urlencode(params).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:255]

    request = Request(f"{STRIPE_API}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(payload).get("error", {})
            message = error.get("message", payload)
        except (ValueError, AttributeError):
            message = payload
        raise StripeAPIError(exc.code, str(message)) from exc
    except URLError as exc:
        raise StripeAPIError(502, f"Stripe API connection failed: {exc.reason}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StripeAPIError(502, "Stripe API returned invalid JSON") from exc


def create_checkout_session(
    idempotency_key: str | None,
    origin: str,
) -> dict[str, Any]:
    settings = config()
    if not settings["publishableKey"].startswith("pk_"):
        raise StripeAPIError(500, "STRIPE_PUBLISHABLE_KEY is not configured correctly")

    params: dict[str, str] = {
        "ui_mode": "elements",
        "mode": "payment",
        "return_url": f"{origin.rstrip('/')}/return.html?session_id={{CHECKOUT_SESSION_ID}}",
        "line_items[0][quantity]": "1",
    }
    if settings["priceId"]:
        params["line_items[0][price]"] = settings["priceId"]
    else:
        params.update(
            {
                "line_items[0][price_data][currency]": settings["currency"],
                "line_items[0][price_data][unit_amount]": str(settings["amount"]),
                "line_items[0][price_data][product_data][name]": settings["productName"],
            }
        )

    payload = stripe_request("POST", "/checkout/sessions", params, idempotency_key)
    client_secret = payload.get("client_secret")
    if not client_secret:
        raise StripeAPIError(502, "Stripe did not return a Checkout Session client secret")
    return {"clientSecret": client_secret, "sessionId": payload.get("id")}


def create_payment_intent(
    plan_id: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create a server-priced PaymentIntent for one of the published plans."""

    settings = config()
    plan = settings["plans"].get(plan_id)
    if not plan:
        raise StripeAPIError(400, "Unknown payment plan")
    if not settings["publishableKey"].startswith("pk_"):
        raise StripeAPIError(500, "STRIPE_PUBLISHABLE_KEY is not configured correctly")

    params = {
        "amount": str(plan["amount"]),
        "currency": plan["currency"],
        "automatic_payment_methods[enabled]": "true",
        "description": plan["productName"],
        "metadata[plan_id]": plan_id,
    }
    payload = stripe_request("POST", "/payment_intents", params, idempotency_key)
    client_secret = payload.get("client_secret")
    if not client_secret:
        raise StripeAPIError(502, "Stripe did not return a PaymentIntent client secret")
    return {
        "clientSecret": client_secret,
        "paymentIntentId": payload.get("id"),
        "amount": plan["amount"],
        "currency": plan["currency"],
        "productName": plan["productName"],
    }


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    values: dict[str, list[str]] = {}
    for item in signature.split(","):
        key, separator, value = item.partition("=")
        if separator:
            values.setdefault(key, []).append(value)

    timestamps = values.get("t", [])
    signatures = values.get("v1", [])
    if not timestamps or not signatures:
        return False
    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False
    if abs(time.time() - timestamp) > 300:
        return False

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


class Handler(BaseHTTPRequestHandler):
    server_version = "StripeCheckoutElementsDemo/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def origin(self) -> str:
        configured = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if configured:
            return configured
        host = self.headers.get("Host", "localhost:4243")
        return f"http://{host}"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key, Stripe-Signature")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            settings = config()
            self.send_json(
                HTTPStatus.OK,
                {
                    "publishableKey": settings["publishableKey"],
                    "amount": settings["amount"],
                    "currency": settings["currency"],
                    "productName": settings["productName"],
                    "priceId": settings["priceId"],
                    "plans": settings["plans"],
                },
            )
            return

        if parsed.path == "/api/payment-intent-status":
            payment_intent_id = parse_qs(parsed.query).get("payment_intent_id", [""])[0]
            if not re.fullmatch(r"pi_[A-Za-z0-9_]+", payment_intent_id):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid payment_intent_id"})
                return
            try:
                payment_intent = stripe_request("GET", f"/payment_intents/{payment_intent_id}")
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "status": payment_intent.get("status"),
                        "amount": payment_intent.get("amount"),
                        "currency": payment_intent.get("currency"),
                    },
                )
            except StripeAPIError as exc:
                self.send_json(exc.status, {"error": exc.message})
            return

        if parsed.path == "/api/session-status":
            session_id = parse_qs(parsed.query).get("session_id", [""])[0]
            if not re.fullmatch(r"cs_[A-Za-z0-9_]+", session_id):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid session_id"})
                return
            try:
                session = stripe_request("GET", f"/checkout/sessions/{session_id}")
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "status": session.get("status"),
                        "paymentStatus": session.get("payment_status"),
                    },
                )
            except StripeAPIError as exc:
                self.send_json(exc.status, {"error": exc.message})
            return

        relative = parsed.path.lstrip("/") or "index.html"
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
        content_type = content_types.get(candidate.suffix.lower(), "application/octet-stream")
        self.send_text(HTTPStatus.OK, candidate.read_bytes(), content_type)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/create-payment-intent":
            try:
                body = self.read_json()
                plan_id = str(body.get("planId", "")).strip()
                result = create_payment_intent(
                    plan_id,
                    self.headers.get("Idempotency-Key"),
                )
                self.send_json(HTTPStatus.OK, result)
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except StripeAPIError as exc:
                self.send_json(exc.status, {"error": exc.message})
            return

        if parsed.path == "/api/create-checkout-session":
            try:
                self.read_json()
                result = create_checkout_session(self.headers.get("Idempotency-Key"), self.origin())
                self.send_json(HTTPStatus.OK, result)
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except StripeAPIError as exc:
                self.send_json(exc.status, {"error": exc.message})
            return

        if parsed.path == "/webhook":
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
            signature = self.headers.get("Stripe-Signature", "")
            if not secret or not verify_webhook_signature(payload, signature, secret):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid webhook signature"})
                return
            try:
                event = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            event_type = event.get("type", "unknown")
            event_id = event.get("id", "unknown")
            print(f"Stripe webhook received: {event_type} ({event_id})")
            # Add fulfillment, subscription, or order-state updates here.  Always
            # acknowledge only after signature verification and parsing succeed.
            self.send_json(HTTPStatus.OK, {"received": True})
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "4243"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Northstar Locate site listening at http://{host}:{port}")
    print("Stripe Payment Element endpoints are ready; configure Stripe keys before accepting payments.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
