#!/usr/bin/env python3
"""CGV 용산아이파크몰 IMAX 예매 오픈 감시기 — 이중 소스.

두 곳을 독립적으로 확인하고, 둘 중 **하나라도** 새 날짜를 발견하면 ntfy로 푸시를 보낸다.

  1차: CGV 공식 BFF API (cgv.co.kr/api/v1/booking/*)
       가장 빠르고 정확하다. 회차별 잔여 좌석 수까지 나온다.
       주의: `www.cgv.co.kr` 은 봇 차단으로 SPA 껍데기만 주고, `www` 없는 아펙스
       도메인 `cgv.co.kr` 만 실제 JSON 을 준다. 또 Cloudflare 가 TLS 지문을 보므로
       curl_cffi 의 Chrome 임퍼소네이션이 필요하다(일반 urllib 은 403).

  2차: 네이버 플레이스 극장 상영시간표 (표준 라이브러리만으로 접근 가능)
       CGV 쪽이 차단되거나 스펙이 바뀌어도 감시가 이어지도록 하는 백업이다.

한쪽이 죽어도 다른 쪽으로 계속 감시한다. 둘 다 죽으면 경고 알림을 보낸다.
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
from datetime import datetime, timedelta, timezone

try:
    from curl_cffi import requests as cffi
except ImportError:  # 네이버 소스만으로도 동작해야 한다
    cffi = None

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 설정
WATCH_FROM = os.environ.get("WATCH_FROM", "2026-08-19")   # 이 날짜(포함) 이후가 열리면 알림

CO_CD = os.environ.get("CO_CD", "A420")                   # CGV 회사코드
SITE_NO = os.environ.get("SITE_NO", "0013")               # CGV 극장코드: 용산아이파크몰
MOVIE_NO = os.environ.get("MOVIE_NO", "30001323")         # CGV 영화코드: 오디세이
SCREEN_NO = os.environ.get("SCREEN_NO", "018")            # 용산 IMAX관 상영관 번호
SCREEN_GRADE = os.environ.get("SCREEN_GRADE", "아이맥스")  # 특별관 등급명 (SCREEN_NO 백업 판정)

PLACE_ID = os.environ.get("PLACE_ID", "12298207")         # 네이버 플레이스: CGV 용산아이파크몰

MOVIE_NAME = os.environ.get("MOVIE_NAME", "오디세이")
SCREEN_NAME = os.environ.get("SCREEN_NAME", "IMAX")

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

STATE_PATH = os.environ.get("STATE_PATH", "state.json")

CGV_API = "https://cgv.co.kr/api/v1/booking"
BOOKING_URL = f"https://cgv.co.kr/cnm/movieBook/cinema?siteNo={SITE_NO}"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


# ---------------------------------------------------------------- 유틸
def log(msg: str) -> None:
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S KST}] {msg}", flush=True)


def ymd_to_iso(ymd: str) -> str:
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def iso_to_ymd(iso: str) -> str:
    return iso.replace("-", "")


def hhmm(raw: str) -> str:
    """'0630' -> '06:30'"""
    raw = (raw or "").strip()
    return f"{raw[:2]}:{raw[2:]}" if len(raw) == 4 else raw


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


# ---------------------------------------------------------------- 소스 1: CGV 공식 API
def cgv_get(endpoint: str, **params) -> list | dict:
    if cffi is None:
        raise RuntimeError("curl_cffi 가 없어 CGV API 를 호출할 수 없다")
    # Cloudflare 가 TLS 지문을 검사하므로 Chrome 을 흉내낸다.
    s = cffi.Session(impersonate="chrome")
    r = s.get(f"{CGV_API}/{endpoint}", params=params, timeout=25, headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR",
        "Referer": "https://www.cgv.co.kr/cnm/movieBook",
    })
    body = r.json()
    if body.get("statusCode") not in (0, "0"):
        raise RuntimeError(f"{endpoint}: {body.get('statusMessage')}")
    return body.get("data") or []


def cgv_open_dates(skip: frozenset[str] = frozenset()) -> set[str]:
    """대상 영화가 이 극장에서 예매 가능한 날짜.

    이 엔드포인트는 movNo 로 이미 걸러진 결과를 주므로 날짜별 재확인이 필요 없다.
    `skip` 은 소스 인터페이스를 맞추기 위한 것이고 여기서는 쓰지 않는다.
    """
    rows = cgv_get("searchSiteScnscYmdListByMov",
                   coCd=CO_CD, siteNo=SITE_NO, movNo=MOVIE_NO)
    dates = {ymd_to_iso(r["scnYmd"]) for r in rows if r.get("scnYmd")}
    if not dates:
        # 상영 중인 영화인데 날짜가 0건이면 정상이 아니다(코드 변경·스펙 변경 의심).
        # 조용히 "열린 날짜 없음"으로 넘기면 고장을 못 잡으므로 실패로 다룬다.
        raise RuntimeError("예매 가능 날짜가 0건 (영화·극장 코드 또는 스펙 변경 의심)")
    return dates


def cgv_showtimes(date_iso: str) -> list[str]:
    """해당 날짜의 대상 영화 × 대상 상영관 회차. 잔여 좌석까지 붙인다."""
    rows = cgv_get("searchSchByMov", coCd=CO_CD, siteNo=SITE_NO, movNo=MOVIE_NO,
                   scnYmd=iso_to_ymd(date_iso), rtctlScopCd="1")
    out = []
    for r in rows:
        if r.get("scnsNo") != SCREEN_NO and r.get("tcscnsGradNm") != SCREEN_GRADE:
            continue
        seats = ""
        free, total = r.get("frSeatCnt"), r.get("stcnt")
        if free is not None and total is not None:
            seats = f" (잔여 {free}/{total}석)"
        out.append(f"{hhmm(r.get('scnsrtTm'))}{seats}")
    return out


# ---------------------------------------------------------------- 소스 2: 네이버 플레이스
def naver_fetch(url: str, timeout: int = 30) -> str:
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


def naver_url(date_iso: str) -> str:
    return f"https://m.place.naver.com/place/{PLACE_ID}/movie?datefilter={date_iso}"


SHOWTIME_PAT = re.compile(
    r'href="https://cgv\.co\.kr/cnm/movieBook/movie\?movNo=(\d+)&amp;scnYmd=(\d{8})'
    r'[^"]*?scnsNo=(\d+)[^"]*"[^>]*>(\d{1,2}:\d{2})<')


def naver_parse_times(html: str, *, imax_only: bool) -> list[str]:
    """페이지에서 대상 영화의 회차 시각을 뽑는다."""
    return [t for mov, _, scns, t in SHOWTIME_PAT.findall(html)
            if mov == MOVIE_NO and (not imax_only or scns == SCREEN_NO)]


def naver_date_tabs(html: str) -> set[str]:
    """상단 날짜 탭 = **극장에** 뭐라도 열린 날짜 (영화별이 아니다)."""
    pat = rf"/theater/{PLACE_ID}/movie\?datefilter=(\d{{4}}-\d{{2}}-\d{{2}})"
    dates = set(re.findall(pat, html))
    if not dates:
        raise RuntimeError("날짜 탭을 파싱하지 못했다 (페이지 구조 변경 의심)")
    return dates


def naver_open_dates(skip: frozenset[str] = frozenset()) -> set[str]:
    """대상 영화가 실제로 상영되는 날짜만 돌려준다.

    날짜 탭은 '극장에 뭐라도 열린 날'이지 '이 영화가 열린 날'이 아니다.
    실제로 8/23 에 다른 영화(명탐정 코난)만 열렸는데 오디세이 예매 오픈으로
    오탐 알림이 나갔다. 그래서 탭 날짜는 후보로만 쓰고, 각 날짜 페이지에서
    대상 영화의 회차가 실제로 있는지 직접 확인한다.

    확인은 날짜마다 요청이 한 번씩 더 드니 알림 대상 구간
    (WATCH_FROM 이후 & 아직 안 알린 날짜)만 검사한다.
    """
    today = datetime.now(KST).date().isoformat()
    home = naver_fetch(naver_url(today))
    dates = set()
    for d in sorted(naver_date_tabs(home)):
        if d < WATCH_FROM or d in skip:
            continue
        html = home if d == today else naver_fetch(naver_url(d))
        if naver_parse_times(html, imax_only=False):
            dates.add(d)
    return dates


def naver_showtimes(date_iso: str) -> list[str]:
    return naver_parse_times(naver_fetch(naver_url(date_iso)), imax_only=True)


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
SOURCES = [
    ("CGV", cgv_open_dates, cgv_showtimes),
    ("네이버", naver_open_dates, naver_showtimes),
]


def run_once(state: dict) -> bool:
    """한 번 검사한다. 상태가 바뀌었으면 True."""
    today = datetime.now(KST).date().isoformat()
    already = frozenset(state.get("notified_dates", []))
    found: dict[str, set[str]] = {}   # 소스명 -> 대상 영화가 열린 날짜
    failures: dict[str, str] = {}

    for name, list_dates, _ in SOURCES:
        try:
            dates = list_dates(already)
            found[name] = dates
            span = f" (최대 {max(dates)})" if dates else ""
            log(f"{name}: {MOVIE_NAME} 예매 가능 {len(dates)}건{span}")
        except Exception as e:
            failures[name] = str(e)
            log(f"{name}: 조회 실패 — {e}")

    if not found:
        # 두 소스가 동시에 죽었다. 하루 한 번만 경고한다.
        log("경고: 모든 소스 조회 실패")
        if state.get("broken_notified_on") != today:
            detail = "\n".join(f"• {k}: {v}" for k, v in failures.items())
            notify("⚠️ CGV 감시기 점검 필요",
                   f"두 소스 모두 조회에 실패했습니다.\n\n{detail}",
                   priority="default", tags="warning", click=BOOKING_URL)
            state["broken_notified_on"] = today
            return True
        return False

    state.pop("broken_notified_on", None)
    state["source_status"] = {name: ("ok" if name in found else failures.get(name, "fail"))
                              for name, _, _ in SOURCES}

    new_by_source = {name: sorted(d for d in dates if d >= WATCH_FROM and d not in already)
                     for name, dates in found.items()}
    new_dates = sorted({d for ds in new_by_source.values() for d in ds})
    seen_max = max((max(d) for d in found.values() if d), default=None)

    if not new_dates:
        state["last_checked"] = datetime.now(KST).isoformat(timespec="seconds")
        if seen_max:
            state["last_seen_max_date"] = seen_max
        return False

    # 새 날짜의 회차 정보를 붙인다. 먼저 성공하는 소스를 쓴다(CGV 우선 — 잔여좌석이 나온다).
    lines = []
    for d in new_dates:
        who = [n for n, ds in new_by_source.items() if d in ds]
        times: list[str] = []
        for name, _, get_times in SOURCES:
            if name not in found:
                continue
            try:
                times = get_times(d)
            except Exception as e:
                log(f"{name}: {d} 회차 조회 실패 — {e}")
                continue
            if times:
                break
        head = f"• {d}  [{'/'.join(who)}]"
        if times:
            lines.append(f"{head}\n  {MOVIE_NAME} {SCREEN_NAME} {len(times)}회차\n  "
                         + "\n  ".join(times))
        else:
            # 영화가 열린 건 확인됐지만 대상 상영관 회차만 아직 없는 경우.
            lines.append(f"{head}\n  {MOVIE_NAME} 상영은 열렸으나 {SCREEN_NAME} 회차 없음")

    title = f"🎬 {MOVIE_NAME} 용산 {SCREEN_NAME} 예매 오픈!"
    body = (f"CGV 용산아이파크몰 {MOVIE_NAME} 예매가 새로 열렸습니다.\n\n"
            + "\n".join(lines)
            + "\n\n지금 바로 예매하세요 →")
    notify(title, body)

    state["notified_dates"] = sorted(set(already) | set(new_dates))
    state["last_checked"] = datetime.now(KST).isoformat(timespec="seconds")
    if seen_max:
        state["last_seen_max_date"] = seen_max
    return True


def main() -> int:
    if os.environ.get("TEST_NOTIFY") == "1":
        ok = notify("🔔 CGV 감시기 테스트",
                    "알림이 정상 동작합니다. 이 메시지가 보이면 설정 완료!",
                    priority="default", tags="white_check_mark")
        return 0 if ok else 1

    duration = int(os.environ.get("POLL_SECONDS", "0"))
    interval = int(os.environ.get("POLL_INTERVAL", "30"))
    deadline = time.monotonic() + duration

    state = load_state()
    changed = False
    while True:
        try:
            changed |= run_once(state)
        except Exception as e:  # 감시기는 절대 죽지 않는다
            log(f"예상치 못한 오류(무시하고 계속): {e!r}")

        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(1, deadline - time.monotonic())))

    if changed:
        save_state(state)
        log("상태 파일 갱신됨")
    else:
        log("변경 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
