"""Multiuser Calreminder web app with Google OpenID Connect sign-in."""

import json
import os
import secrets
import hashlib
import base64
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from cryptography.fernet import Fernet
from flask import Flask, jsonify, redirect, request, send_from_directory, session
from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import MAX_FEED_BYTES, parse_ics


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web_static"
BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
ENCRYPTION_KEY = os.environ.get("FEED_ENCRYPTION_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not all((BASE_URL, CLIENT_ID, CLIENT_SECRET, SESSION_SECRET, ENCRYPTION_KEY, DATABASE_URL)):
    raise RuntimeError("APP_BASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET, FEED_ENCRYPTION_KEY, DATABASE_URL are required")
if not (BASE_URL.startswith("https://") or BASE_URL.startswith("http://localhost:")
        or BASE_URL.startswith("http://127.0.0.1:")):
    raise RuntimeError("APP_BASE_URL must use HTTPS, except on localhost")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if DATABASE_URL.startswith("sqlite:///data/"):
    (ROOT / "data").mkdir(exist_ok=True)

fernet = Fernet(ENCRYPTION_KEY.encode())
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
metadata = MetaData()
calendars = Table(
    "calendars", metadata,
    Column("user_id", String(255), primary_key=True),
    Column("feed_url", Text, nullable=False),
    Column("events", Text, nullable=False),
    Column("completed", Text, nullable=False),
    Column("last_sync", String(50)),
)
extension_codes = Table(
    "extension_codes", metadata,
    Column("code_hash", String(64), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("challenge", String(255), nullable=False),
    Column("expires_at", String(50), nullable=False),
)
extension_tokens = Table(
    "extension_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("expires_at", String(50), nullable=False),
)
metadata.create_all(engine)

app = Flask(__name__)
app.secret_key = SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=BASE_URL.startswith("https://"),
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=100_000,
)
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@app.after_request
def private_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return response


def signed_in(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify(error="로그인이 필요합니다."), 401
            return redirect("/welcome")
        return view(*args, **kwargs)
    return wrapped


def require_csrf():
    value = request.headers.get("X-CSRF-Token", "")
    if not value or not secrets.compare_digest(value, session.get("csrf", "")):
        return jsonify(error="보안 토큰이 올바르지 않습니다. 새로고침해 주세요."), 403
    return None


def get_calendar(user_id):
    with engine.connect() as conn:
        row = conn.execute(select(calendars).where(calendars.c.user_id == user_id)).mappings().first()
    return dict(row) if row else None


def save_calendar(user_id, state):
    values = {
        "user_id": user_id,
        "feed_url": state["feed_url"],
        "events": json.dumps(state["events"], ensure_ascii=False),
        "completed": json.dumps(state["completed"], ensure_ascii=False),
        "last_sync": state["last_sync"],
    }
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(calendars).values(**values).on_conflict_do_update(
        index_elements=[calendars.c.user_id],
        set_={key: value for key, value in values.items() if key != "user_id"},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def public_state(row):
    if not row:
        return {"configured": False, "source": "", "events": [], "completed": {}, "last_sync": None}
    url = fernet.decrypt(row["feed_url"].encode()).decode()
    return {
        "configured": True,
        "source": urllib.parse.urlsplit(url).hostname or "",
        "events": json.loads(row["events"]),
        "completed": json.loads(row["completed"]),
        "last_sync": row["last_sync"],
    }


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def extension_user():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:]
    if len(token) > 200:
        return None
    with engine.connect() as conn:
        row = conn.execute(select(extension_tokens).where(
            extension_tokens.c.token_hash == token_hash(token))).mappings().first()
    if not row or row["expires_at"] <= datetime.now(timezone.utc).isoformat():
        return None
    return row["user_id"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch_feed(url):
    parsed = urllib.parse.urlsplit(url)
    allowed = [host.strip().lower() for host in os.environ.get("ALLOWED_FEED_HOSTS", "ys.learnus.org").split(",")]
    if (parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or parsed.path != "/calendar/export_execute.php"):
        raise ValueError("허용된 LearnUs iCalendar 링크를 입력해 주세요.")
    req = urllib.request.Request(url, headers={"User-Agent": "Calreminder/2.0", "Accept": "text/calendar"})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
        body = response.read(MAX_FEED_BYTES + 1)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError("캘린더 파일이 너무 큽니다.")
        return parse_ics(body.decode(response.headers.get_content_charset() or "utf-8", errors="replace").lstrip("\ufeff"))


@app.get("/welcome")
def welcome():
    if session.get("user_id"):
        return redirect("/")
    return send_from_directory(STATIC, "welcome.html")


@app.get("/login")
def login():
    if session.get("user_id"):
        return redirect("/")
    return oauth.google.authorize_redirect(BASE_URL + "/auth/callback")


@app.get("/extension/login")
def extension_login():
    redirect_uri = request.args.get("redirect_uri", "")
    challenge = request.args.get("code_challenge", "")
    parsed = urllib.parse.urlsplit(redirect_uri)
    if (parsed.scheme != "https" or not re.fullmatch(r"[a-p]{32}\.chromiumapp\.org", parsed.hostname or "")
            or parsed.path != "/oauth" or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)):
        return "확장 프로그램 로그인 요청이 올바르지 않습니다.", 400
    session["extension_flow"] = {"redirect_uri": redirect_uri, "challenge": challenge}
    return oauth.google.authorize_redirect(BASE_URL + "/auth/callback")


@app.get("/auth/callback")
def auth_callback():
    token = oauth.google.authorize_access_token()
    profile = token.get("userinfo")
    if not profile or not profile.get("sub") or not profile.get("email_verified"):
        return "Google 계정을 확인할 수 없습니다.", 403
    extension_flow = session.get("extension_flow")
    session.clear()
    if extension_flow:
        code = secrets.token_urlsafe(32)
        with engine.begin() as conn:
            conn.execute(extension_codes.insert().values(
                code_hash=token_hash(code), user_id=profile["sub"],
                challenge=extension_flow["challenge"],
                expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            ))
        return redirect(extension_flow["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code}))
    session["user_id"] = profile["sub"]
    session["name"] = profile.get("given_name") or profile.get("name") or "사용자"
    session["csrf"] = secrets.token_urlsafe(32)
    return redirect("/")


@app.post("/logout")
@signed_in
def logout():
    error = require_csrf()
    if error:
        return error
    session.clear()
    return jsonify(ok=True)


@app.get("/")
@signed_in
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/style.css")
@app.get("/app.js")
def assets():
    return send_from_directory(STATIC, request.path.lstrip("/"))


@app.get("/api/state")
@signed_in
def api_state():
    result = public_state(get_calendar(session["user_id"]))
    result["csrf"] = session["csrf"]
    result["name"] = session["name"]
    return jsonify(result)


@app.post("/api/source")
@app.post("/api/refresh")
@app.post("/api/complete")
@signed_in
def api_mutate():
    error = require_csrf()
    if error:
        return error
    return mutate(session["user_id"], request.path.rsplit("/", 1)[-1])


def mutate(user_id, action):
    payload = request.get_json(silent=True) or {}
    row = get_calendar(user_id)
    if action == "source":
        url = str(payload.get("url", "")).strip()
        try:
            events = fetch_feed(url)
        except (ValueError, OSError, UnicodeError, urllib.error.HTTPError):
            return jsonify(error="캘린더 링크를 읽지 못했습니다. 주소와 접근 권한을 확인해 주세요."), 400
        old_url = fernet.decrypt(row["feed_url"].encode()).decode() if row else ""
        state = {
            "feed_url": fernet.encrypt(url.encode()).decode(),
            "events": events,
            "completed": json.loads(row["completed"]) if row and old_url == url else {},
            "last_sync": datetime.now(timezone.utc).isoformat(),
        }
    elif not row:
        return jsonify(error="먼저 캘린더 링크를 설정해 주세요."), 400
    elif action == "refresh":
        url = fernet.decrypt(row["feed_url"].encode()).decode()
        try:
            events = fetch_feed(url)
        except (ValueError, OSError, UnicodeError, urllib.error.HTTPError):
            return jsonify(error="캘린더를 동기화하지 못했습니다. 잠시 후 다시 시도해 주세요."), 400
        state = dict(feed_url=row["feed_url"], events=events,
                     completed=json.loads(row["completed"]),
                     last_sync=datetime.now(timezone.utc).isoformat())
    else:
        event_id = payload.get("id")
        events = json.loads(row["events"])
        if not isinstance(event_id, str) or not any(item["id"] == event_id for item in events):
            return jsonify(error="일정을 찾을 수 없습니다."), 400
        completed = json.loads(row["completed"])
        if payload.get("done") is True:
            completed[event_id] = datetime.now(timezone.utc).isoformat()
        else:
            completed.pop(event_id, None)
        state = dict(feed_url=row["feed_url"], events=events,
                     completed=completed, last_sync=row["last_sync"])
    save_calendar(user_id, state)
    result = public_state({"feed_url": state["feed_url"],
                           "events": json.dumps(state["events"]),
                           "completed": json.dumps(state["completed"]),
                           "last_sync": state["last_sync"]})
    return jsonify(result)


@app.post("/api/extension/token")
def exchange_extension_code():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", ""))
    verifier = str(payload.get("verifier", ""))
    if len(code) > 200 or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier):
        return jsonify(error="인증 코드가 올바르지 않습니다."), 400
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with engine.begin() as conn:
        row = conn.execute(select(extension_codes).where(
            extension_codes.c.code_hash == token_hash(code))).mappings().first()
        if not row or row["expires_at"] <= datetime.now(timezone.utc).isoformat() or not secrets.compare_digest(row["challenge"], challenge):
            return jsonify(error="인증 코드가 만료되었거나 올바르지 않습니다."), 400
        conn.execute(extension_codes.delete().where(extension_codes.c.code_hash == row["code_hash"]))
        token = secrets.token_urlsafe(48)
        conn.execute(extension_tokens.insert().values(
            token_hash=token_hash(token), user_id=row["user_id"],
            expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        ))
    return jsonify(token=token)


@app.get("/api/extension/state")
def extension_state():
    user_id = extension_user()
    if not user_id:
        return jsonify(error="로그인이 필요합니다."), 401
    return jsonify(public_state(get_calendar(user_id)))


@app.post("/api/extension/source")
@app.post("/api/extension/refresh")
@app.post("/api/extension/complete")
def extension_mutate():
    user_id = extension_user()
    if not user_id:
        return jsonify(error="로그인이 필요합니다."), 401
    return mutate(user_id, request.path.rsplit("/", 1)[-1])


@app.post("/api/extension/logout")
def extension_logout():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        with engine.begin() as conn:
            conn.execute(extension_tokens.delete().where(
                extension_tokens.c.token_hash == token_hash(header[7:])))
    return jsonify(ok=True)


@app.get("/health")
def health():
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
