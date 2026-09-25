#!/usr/bin/env python3
"""Local LearnUs calendar checklist. Uses only the Python standard library."""

import argparse
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_FILE = DATA / "state.json"
STATIC = ROOT / "static"
LOCK = threading.RLock()
MAX_FEED_BYTES = 5_000_000


def save_state(state):
    DATA.mkdir(exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    temp.replace(STATE_FILE)


def load_state():
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    return {"feed_url": "", "events": [], "completed": {}, "last_sync": None}


def valid_feed_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


def unescape(value):
    return re.sub(r"\\([nN,;\\])", lambda match: {
        "n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"
    }[match.group(1)], value)


def parse_date(value, params):
    if not value:
        return None, False
    all_day = params.get("VALUE") == "DATE" or bool(re.fullmatch(r"\d{8}", value))
    try:
        if all_day:
            return datetime.strptime(value[:8], "%Y%m%d").date().isoformat(), True
        utc = value.endswith("Z")
        raw = value[:-1] if utc else value
        fmt = "%Y%m%dT%H%M%S" if len(raw) == 15 else "%Y%m%dT%H%M"
        dt = datetime.strptime(raw, fmt)
        if utc:
            dt = dt.replace(tzinfo=timezone.utc)
        elif params.get("TZID"):
            try:
                dt = dt.replace(tzinfo=ZoneInfo(params["TZID"].strip('"')))
            except ZoneInfoNotFoundError:
                pass
        return dt.isoformat(), False
    except ValueError:
        return None, False


def parse_ics(content):
    if "BEGIN:VCALENDAR" not in content:
        raise ValueError("유효한 iCalendar 데이터가 아닙니다.")
    lines = re.sub(r"\r?\n[ \t]", "", content).splitlines()
    events = []
    properties = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            properties = {}
        elif line == "END:VEVENT" and properties is not None:
            def prop(name):
                return properties.get(name, ("", {}))

            start, all_day = parse_date(*prop("DTSTART"))
            end, _ = parse_date(*prop("DTEND"))
            uid = prop("UID")[0]
            if start:
                recurrence = prop("RECURRENCE-ID")[0]
                key = uid + "|" + (recurrence or start)
                if not uid:
                    key = prop("SUMMARY")[0] + "|" + start
                link = unescape(prop("URL")[0])
                if urllib.parse.urlsplit(link).scheme not in ("http", "https"):
                    link = ""
                events.append({
                    "id": key,
                    "title": unescape(prop("SUMMARY")[0]) or "제목 없는 일정",
                    "description": unescape(prop("DESCRIPTION")[0]),
                    "location": unescape(prop("LOCATION")[0]),
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "url": link,
                })
            properties = None
        elif properties is not None and ":" in line:
            key, value = line.split(":", 1)
            parts = key.split(";")
            params = {}
            for item in parts[1:]:
                if "=" in item:
                    k, v = item.split("=", 1)
                    params[k.upper()] = v
            properties[parts[0].upper()] = (value, params)
    events.sort(key=lambda event: event["start"])
    return events


def fetch_feed(url):
    if not valid_feed_url(url):
        raise ValueError("HTTPS 캘린더 링크를 입력해 주세요.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Calreminder/1.0", "Accept": "text/calendar, text/plain;q=0.8"
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise ValueError("안전하지 않은 주소로 이동했습니다.")
        body = response.read(MAX_FEED_BYTES + 1)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError("캘린더 파일이 너무 큽니다.")
        charset = response.headers.get_content_charset() or "utf-8"
        return parse_ics(body.decode(charset, errors="replace").lstrip("\ufeff"))


def public_state(state):
    url = state.get("feed_url", "")
    return {
        "configured": bool(url),
        "source": urllib.parse.urlsplit(url).hostname or "",
        "events": state.get("events", []),
        "completed": state.get("completed", {}),
        "last_sync": state.get("last_sync"),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        # Never log feed URLs or calendar content.
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("요청이 너무 큽니다.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/state":
            with LOCK:
                self._json(public_state(load_state()))
            return
        if path == "/":
            path = "/index.html"
        files = {"/index.html": ("index.html", "text/html; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if path not in files:
            self.send_error(404)
            return
        name, content_type = files[path]
        body = (STATIC / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            payload = self._read_json()
            with LOCK:
                state = load_state()
                if self.path == "/api/source":
                    url = str(payload.get("url", "")).strip()
                    if not valid_feed_url(url):
                        raise ValueError("HTTPS 캘린더 링크를 입력해 주세요.")
                    events = fetch_feed(url)
                    if url != state.get("feed_url"):
                        state["completed"] = {}
                    state.update(feed_url=url, events=events,
                                 last_sync=datetime.now(timezone.utc).isoformat())
                elif self.path == "/api/refresh":
                    if not state.get("feed_url"):
                        raise ValueError("먼저 캘린더 링크를 설정해 주세요.")
                    state["events"] = fetch_feed(state["feed_url"])
                    state["last_sync"] = datetime.now(timezone.utc).isoformat()
                elif self.path == "/api/complete":
                    event_id = payload.get("id")
                    if not isinstance(event_id, str) or not any(
                        event["id"] == event_id for event in state.get("events", [])
                    ):
                        raise ValueError("일정을 찾을 수 없습니다.")
                    completed = state.setdefault("completed", {})
                    if bool(payload.get("done")):
                        completed[event_id] = datetime.now(timezone.utc).isoformat()
                    else:
                        completed.pop(event_id, None)
                else:
                    self._json({"error": "찾을 수 없는 요청입니다."}, 404)
                    return
                save_state(state)
                self._json(public_state(state))
        except (ValueError, urllib.error.URLError, TimeoutError, UnicodeError) as exc:
            message = str(exc)
            if isinstance(exc, urllib.error.URLError):
                message = "캘린더에 연결할 수 없습니다. 링크와 인터넷 연결을 확인해 주세요."
            self._json({"error": message}, 400)
        except Exception:
            self._json({"error": "요청을 처리하지 못했습니다."}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"Calreminder: {address}", flush=True)
    print("종료하려면 이 창에서 Ctrl+C를 누르세요.", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
