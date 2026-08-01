#!/usr/bin/env python3
"""CGV 용산아이파크몰 IMAX 예매 오픈 감시기.

네이버 플레이스의 극장 상영시간표 페이지를 읽어 '예매 가능한 날짜' 목록을 뽑는다.
WATCH_FROM 이후 날짜가 목록에 나타나면 = 그 날짜 예매가 열린 것이므로 ntfy로 푸시를 보낸다.

CGV 본사이트(cgv.co.kr)는 Cloudflare + 자체 봇 차단으로 서버에서 접근이 불가능하다.
(일반 HTTP 클라이언트 403, 헤드리스 크로미움도 "비정상적으로 접속" 차단)
네이버 플레이스는 같은 데이터를 서버 렌더링으로 내려주고 차단이 없어서 이쪽을 쓴다.

의존성 없음 — 파이썬 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 설정
PLACE_ID = os.environ.get("PLACE_ID", "12298207")          # 네이버 플레이스: CGV 용산아이파크몰
WATCH_FROM = os.environ.get("WATCH_FROM", "2026-08-19")     # 이 날짜(포함) 이후가 열리면 알림
MOVIE_NO = os.environ.get("MOVIE_NO", "30001323")           # CGV 영화코드: 오디세이
MOVIE_NAME = os.environ.get("MOVIE_NAME", "오디세이")
SCREEN_NO = os.environ.get("SCREEN_NO", "018")              # 용산 IMAX관 상영관 번호
SCREEN_NAME = os.environ.get("SCREEN_NAME", "IMAX")

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

STATE_PATH = os.environ.get("STATE_PATH", "state.json")

BOOKING_URL = (
    "https://cgv.co.kr/cnm/movieBook/cinema?siteNo=0013"
)

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


# ---------------------------------------------------------------- 유틸
def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S KST}] {msg}", flush=True)


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------- 파싱
def theater_url(day: str) -> str:
    return f"https://m.place.naver.com/place/{PLACE_ID}/movie?datefilter={day}"


def parse_bookable_dates(html: str) -> list[str]:
    """상단 날짜 탭에 노출되는 = 예매가 열린 날짜 목록."""
    pat = rf"/theater/{PLACE_ID}/movie\?datefilter=(\d{{4}}-\d{{2}}-\d{{2}})"
    return sorted(set(re.findall(pat, html)))


def parse_showtimes(html: str) -> list[dict]:
    """예매 링크에서 (영화코드, 날짜, 상영관번호, 시각)을 뽑는다."""
    pat = (r'href="https://cgv\.co\.kr/cnm/movieBook/movie\?movNo=(\d+)&amp;scnYmd=(\d{8})'
           r'[^"]*?scnsNo=(\d+)[^"]*"[^>]*>(\d{1,2}:\d{2})<')
    out = []
    for mov_no, ymd, scns_no, hhmm in re.findall(pat, html):
        out.append({"movNo": mov_no, "date": ymd, "screenNo": scns_no, "time": hhmm})
    return out


def target_showtimes(html: str) -> list[str]:
    """해당 날짜의 대상 영화 × 대상 상영관 회차 시각."""
    return [s["time"] for s in parse_showtimes(html)
            if s["movNo"] == MOVIE_NO and s["screenNo"] == SCREEN_NO]


# ---------------------------------------------------------------- 알림
def notify(title: str, body: str, *, priority: str = "urgent",
           tags: str = "clapper", click: str = BOOKING_URL) -> bool:
    if os.environ.get("DRY_RUN") == "1" or not NTFY_TOPIC:
        why = "DRY_RUN" if os.environ.get("DRY_RUN") == "1" else "NTFY_TOPIC 미설정"
        log(f"[{why}] 실제 전송 없이 아래 알림을 보냈을 것이다:\n"
            f"  ── {title}\n"
            + "\n".join(f"  │ {ln}" for ln in body.splitlines())
            + f"\n  └─ 링크: {click}")
        return False
    url = f"{NTFY_SERVER}/{urllib.parse.quote(NTFY_TOPIC)}"
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={
        # ntfy 헤더는 latin-1 만 허용하므로 한글 제목은 RFC2047 로 인코딩한다.
        "Title": "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?=",
        "Priority": priority,
        "Tags": tags,
        "Click": click,
        "Content-Type": "text/plain; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log(f"알림 전송 완료: {title}")
        return True
    except urllib.error.URLError as e:
        log(f"알림 전송 실패: {e}")
        return False


# ---------------------------------------------------------------- 본체
def run_once(state: dict) -> bool:
    """한 번 검사한다. 상태가 바뀌었으면 True."""
    today = datetime.now(KST).date().isoformat()
    html = fetch(theater_url(today))
    dates = parse_bookable_dates(html)

    if not dates:
        # 페이지 구조가 바뀌었거나 차단된 경우. 하루 한 번만 경고한다.
        log("경고: 날짜 목록을 파싱하지 못했다.")
        if state.get("broken_notified_on") != today:
            notify("⚠️ CGV 감시기 점검 필요",
                   "네이버 플레이스에서 상영 날짜 목록을 읽지 못했습니다.\n"
                   "페이지 구조가 바뀌었을 수 있습니다.",
                   priority="default", tags="warning",
                   click=theater_url(today))
            state["broken_notified_on"] = today
            return True
        return False

    state.pop("broken_notified_on", None)
    log(f"예매 가능 날짜 {len(dates)}건: {dates[0]} ~ {dates[-1]}")

    new_dates = [d for d in dates
                 if d >= WATCH_FROM and d not in state.get("notified_dates", [])]
    if not new_dates:
        state["last_checked"] = datetime.now(KST).isoformat(timespec="seconds")
        state["last_seen_max_date"] = dates[-1]
        return False

    # 열린 날짜들 중 대상 영화 IMAX 회차를 확인한다.
    lines = []
    for d in new_dates:
        try:
            times = target_showtimes(fetch(theater_url(d)))
        except urllib.error.URLError as e:
            log(f"{d} 상영시간표 조회 실패: {e}")
            times = []
        if times:
            lines.append(f"• {d} — {SCREEN_NAME} {len(times)}회차: {', '.join(times)}")
        else:
            lines.append(f"• {d} — {MOVIE_NAME} {SCREEN_NAME} 회차 없음(다른 관은 열렸을 수 있음)")

    title = f"🎬 {MOVIE_NAME} 용산 {SCREEN_NAME} 예매 오픈!"
    body = ("CGV 용산아이파크몰 예매가 새로 열렸습니다.\n\n"
            + "\n".join(lines)
            + "\n\n지금 바로 예매하세요 →")
    notify(title, body)

    state.setdefault("notified_dates", [])
    state["notified_dates"] = sorted(set(state["notified_dates"]) | set(new_dates))
    state["last_checked"] = datetime.now(KST).isoformat(timespec="seconds")
    state["last_seen_max_date"] = dates[-1]
    return True


def main() -> int:
    if os.environ.get("TEST_NOTIFY") == "1":
        ok = notify(f"🔔 CGV 감시기 테스트", "알림이 정상 동작합니다. 이 메시지가 보이면 설정 완료!",
                    priority="default", tags="white_check_mark")
        return 0 if ok else 1

    duration = int(os.environ.get("POLL_SECONDS", "0"))
    interval = int(os.environ.get("POLL_INTERVAL", "45"))
    deadline = time.monotonic() + duration

    state = load_state()
    changed = False
    while True:
        try:
            changed |= run_once(state)
        except urllib.error.URLError as e:
            log(f"조회 실패(무시하고 계속): {e}")
        except Exception as e:  # 감시기는 절대 죽지 않는다
            log(f"예상치 못한 오류(무시하고 계속): {e!r}")

        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(1, deadline - time.monotonic())))

    if changed:
        save_state(state)
        log("상태 파일 갱신됨")
    else:
        # 마지막 확인 시각만 갱신해도 커밋이 발생하지 않도록 저장하지 않는다.
        log("변경 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
