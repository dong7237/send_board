import json
import os
import re
import smtplib
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from bs4 import BeautifulSoup


BASE_URL = os.environ.get("NOTICE_URL", "https://bizgrad.hanyang.ac.kr/nt1").strip()
STATE_PATH = os.environ.get("STATE_PATH", "state.json").strip()

# First pages are enough for "daily check". Increase if you want.
PAGES = int(os.environ.get("PAGES", "3"))

SMTP_TO = os.environ.get("SMTP_TO", "").strip()
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.naver.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "").strip().lower()
SMTP_TIMEOUT_SEC = int(os.environ.get("SMTP_TIMEOUT_SEC", "30"))
SMTP_DEBUG = os.environ.get("SMTP_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Notice:
    message_id: str
    title: str
    url: str
    category: str | None = None
    date: str | None = None


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"initialized": False, "seen_ids": [], "updated_at": None}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"initialized": False, "seen_ids": [], "updated_at": None}
    data.setdefault("initialized", False)
    data.setdefault("seen_ids", [])
    data.setdefault("updated_at", None)
    if not isinstance(data["seen_ids"], list):
        data["seen_ids"] = []
    return data


def save_state(path: str, state: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def fetch_html(url: str, *, timeout_sec: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; notice-checker/1.0)",
            "Accept-Language": "ko,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


def list_url(page: int) -> str:
    params = {
        "p_p_id": "kr_ac_hanyang_bbs_web_portlet_BbsPortlet",
        "p_p_lifecycle": "0",
        "p_p_state": "normal",
        "p_p_mode": "view",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_action": "view",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_sDisplayType": "1",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_cur": str(page),
    }
    return f"{BASE_URL}?{urllib.parse.urlencode(params)}"


def message_url(message_id: str) -> str:
    params = {
        "p_p_id": "kr_ac_hanyang_bbs_web_portlet_BbsPortlet",
        "p_p_lifecycle": "0",
        "p_p_state": "normal",
        "p_p_mode": "view",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_action": "view_message",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_sDisplayType": "1",
        "_kr_ac_hanyang_bbs_web_portlet_BbsPortlet_messageId": str(message_id),
    }
    return f"{BASE_URL}?{urllib.parse.urlencode(params)}"


_VIEW_MESSAGE_RE = re.compile(r"viewMessage\((\d+),")
_DATE_TEXT_RE = re.compile(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}")


def parse_notices(html: str) -> list[Notice]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[Notice] = []

    for row in soup.select("div.hyu-list-body-item"):
        a = row.select_one('a[onclick*="BbsPortlet_viewMessage("]')
        if not a:
            continue

        onclick = a.get("onclick", "")
        match = _VIEW_MESSAGE_RE.search(onclick)
        if not match:
            continue
        message_id = match.group(1)

        title = " ".join(a.get_text(" ", strip=True).split())
        if not title:
            continue

        category = None
        date = None
        col = a.find_parent("div", class_="hyu-list-body-item-col")
        meta = col.find("p") if col else None
        if meta:
            badge = meta.select_one("span.hyu-badge")
            if badge:
                category = badge.get_text(" ", strip=True) or None
            for span in meta.select("span.date"):
                text = span.get_text(" ", strip=True)
                if _DATE_TEXT_RE.search(text):
                    date = text

        items.append(
            Notice(
                message_id=message_id,
                title=title,
                url=message_url(message_id),
                category=category,
                date=date,
            )
        )

    # Dedup (just in case)
    seen: set[str] = set()
    out: list[Notice] = []
    for item in items:
        if item.message_id in seen:
            continue
        seen.add(item.message_id)
        out.append(item)
    return out


def _default_security_for_port(port: int) -> str:
    if port == 465:
        return "ssl"
    if port == 587:
        return "starttls"
    return "plain"


def _normalize_security(security: str, *, port: int) -> str:
    sec = (security or "").strip().lower()
    if not sec:
        return _default_security_for_port(port)
    if sec in {"starttls", "tls"}:
        return "starttls"
    if sec in {"ssl", "smtps"}:
        return "ssl"
    if sec in {"plain", "none"}:
        return "plain"
    return sec


def _debug(msg: str) -> None:
    if SMTP_DEBUG:
        print(f"[smtp] {msg}", file=sys.stderr)


def _unique_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _guess_email_domain(host: str) -> str | None:
    host = (host or "").lower()
    if host.endswith("naver.com"):
        return "naver.com"
    if host.endswith("gmail.com") or host.endswith("google.com"):
        return "gmail.com"
    if host.endswith("outlook.com") or host.endswith("office365.com"):
        return "outlook.com"
    return None


def _ensure_email_address(value: str, *, host: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    if "@" in value:
        return value
    domain = os.environ.get("SMTP_USER_DOMAIN", "").strip() or _guess_email_domain(host)
    return f"{value}@{domain}" if domain else value


def _password_candidates(password: str, *, host: str) -> list[str]:
    candidates = [password]
    if host.lower().endswith("naver.com"):
        compact = "".join(password.split())
        if compact:
            candidates.append(compact)
    return _unique_keep_order(candidates)


def _connection_profiles(*, host: str, port: int, security: str) -> list[tuple[int, str]]:
    profiles = [(port, _normalize_security(security, port=port))]
    if host.lower().endswith("naver.com"):
        profiles.extend([(587, "starttls"), (465, "ssl")])
    unique_profiles: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for profile in profiles:
        if profile in seen:
            continue
        seen.add(profile)
        unique_profiles.append(profile)
    return unique_profiles


def _smtp_connect(*, host: str, port: int, security: str) -> smtplib.SMTP:
    security = _normalize_security(security, port=port)

    if security == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SEC)
        smtp.ehlo()
        return smtp

    smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SEC)
    smtp.ehlo()
    if security == "starttls":
        smtp.starttls()
        smtp.ehlo()
    return smtp


def send_email(*, subject: str, body: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("SMTP_USER / SMTP_PASS are missing. Set GitHub Secrets first.")
    if not SMTP_TO:
        raise RuntimeError("SMTP_TO is missing. Set GitHub Secrets first.")

    msg = EmailMessage()
    from_addr = SMTP_FROM or SMTP_USER
    msg["From"] = _ensure_email_address(from_addr, host=SMTP_HOST)
    msg["To"] = SMTP_TO
    msg["Subject"] = subject
    msg.set_content(body)

    login_user_candidates = [SMTP_USER.strip()]
    if "@" not in SMTP_USER:
        login_user_candidates.append(_ensure_email_address(SMTP_USER, host=SMTP_HOST))
    login_user_candidates = _unique_keep_order(login_user_candidates)

    password_candidates = _password_candidates(SMTP_PASS, host=SMTP_HOST)
    connection_profiles = _connection_profiles(host=SMTP_HOST, port=SMTP_PORT, security=SMTP_SECURITY)

    last_auth_error: Exception | None = None
    last_error: Exception | None = None

    for port, security in connection_profiles:
        for login_user in login_user_candidates:
            for password in password_candidates:
                try:
                    _debug(f"try host={SMTP_HOST} port={port} security={security} user={login_user}")
                    with _smtp_connect(host=SMTP_HOST, port=port, security=security) as smtp:
                        smtp.login(login_user, password)
                        smtp.send_message(msg)
                        return
                except smtplib.SMTPAuthenticationError as e:
                    last_auth_error = e
                    last_error = e
                    _debug(f"auth failed: code={getattr(e, 'smtp_code', 'unknown')}")
                    continue
                except (smtplib.SMTPException, OSError) as e:
                    last_error = e
                    _debug(f"connection/send failed: {e}")
                    continue

    if last_auth_error is not None:
        raise RuntimeError(
            "SMTP 인증 실패(535). "
            "아이디 형식/비밀번호 공백 제거/465(SSL)·587(STARTTLS) 조합까지 재시도했지만 실패했습니다. "
            "NAVER_SMTP_USER는 반드시 'id@naver.com', NAVER_SMTP_PASS는 '앱 비밀번호(메일)'만 사용하세요. "
            "네이버 메일 설정에서 IMAP/SMTP 사용 ON, GitHub Secrets 재저장 후 워크플로를 다시 실행하세요."
        ) from last_auth_error

    if last_error is not None:
        raise RuntimeError(f"SMTP 연결/전송 실패: {last_error}") from last_error

    raise RuntimeError("SMTP 전송 실패: 원인을 확인할 수 없습니다.")


def format_email(notices: list[Notice]) -> tuple[str, str]:
    subject = f"[한양대 경영대학원 공지] 새 글 {len(notices)}개"
    lines: list[str] = []
    lines.append(f"새 공지 {len(notices)}개가 있습니다.")
    lines.append("")
    for n in notices:
        head = "- "
        if n.category:
            head += f"[{n.category}] "
        if n.date:
            head += f"{n.date} | "
        head += n.title
        lines.append(head)
        lines.append(f"  {n.url}")
    lines.append("")
    kst = timezone(timedelta(hours=9), "KST")
    checked_at = datetime.now(timezone.utc).astimezone(kst).strftime("%Y-%m-%d %H:%M %Z")
    lines.append(f"(자동 확인 시간: {checked_at})")
    return subject, "\n".join(lines)


def main() -> int:
    state = load_state(STATE_PATH)
    old_seen_ids: list[str] = [str(x) for x in state.get("seen_ids", [])]
    old_seen_set = set(old_seen_ids)

    notices: list[Notice] = []
    for page in range(1, max(PAGES, 1) + 1):
        html = fetch_html(list_url(page))
        notices.extend(parse_notices(html))

    if not notices:
        raise RuntimeError("No notices parsed. Site HTML may have changed.")

    new_notices = [n for n in notices if n.message_id not in old_seen_set]

    # First run: do not send email, only save state.
    if not state.get("initialized", False):
        state["initialized"] = True
        state["seen_ids"] = [n.message_id for n in notices]
        state["updated_at"] = _now_iso_utc()
        save_state(STATE_PATH, state)
        print("Initialized state.json (no email on first run).")
        return 0

    if not new_notices:
        print("No new notices.")
        return 0

    subject, body = format_email(new_notices)
    send_email(subject=subject, body=body)
    print(f"Sent email: {len(new_notices)} new notices.")

    # Update state (cap size to avoid endless growth)
    max_ids = int(os.environ.get("MAX_SEEN_IDS", "2000"))
    merged_ids: list[str] = []
    merged_set: set[str] = set()
    for n in notices:
        if n.message_id in merged_set:
            continue
        merged_set.add(n.message_id)
        merged_ids.append(n.message_id)
        if len(merged_ids) >= max_ids:
            break
    if len(merged_ids) < max_ids:
        for mid in old_seen_ids:
            if mid in merged_set:
                continue
            merged_set.add(mid)
            merged_ids.append(mid)
            if len(merged_ids) >= max_ids:
                break

    state["seen_ids"] = merged_ids
    state["updated_at"] = _now_iso_utc()
    save_state(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
