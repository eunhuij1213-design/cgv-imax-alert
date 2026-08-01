# CGV 용산아이파크몰 IMAX 예매 오픈 알림

`오디세이` 8/19 이후 회차 예매가 열리는 순간 폰으로 푸시를 보낸다.

- **컴퓨터가 꺼져 있어도 동작** — GitHub Actions(클라우드)에서 돈다.
- **LLM 토큰 0** — 순수 파이썬 스크립트. 외부 의존성도 없다(표준 라이브러리만).
- **비용 0** — 공개 저장소면 Actions 무료, ntfy.sh도 무료.
- **감시 주기 약 30초** — 크론은 5분마다 뜨지만, 한 번 뜰 때마다 4분 30초 동안 30초 간격으로 확인한다.

## 어떻게 감지하나

CGV 본사이트(`cgv.co.kr`)는 Cloudflare + 자체 봇 차단이 걸려 있어 서버에서 접근이 안 된다.
일반 HTTP 클라이언트는 403, 헤드리스 크로미움은 "비정상적으로 CGV에 접속한 것이 확인되어
이용이 제한되었어요" 페이지가 뜬다.

대신 **네이버 플레이스**가 같은 상영시간표를 서버 렌더링으로 내려주고 봇 차단이 없다.

```
https://m.place.naver.com/place/12298207/movie?datefilter=YYYY-MM-DD
```

이 페이지의 날짜 탭 = 예매가 열린 날짜다. 지금은 `2026-08-01 ~ 2026-08-18`까지만 있고,
여기에 `2026-08-19`가 나타나는 순간이 곧 예매 오픈이다. 그때 해당 날짜 페이지를 한 번 더 읽어
오디세이(`movNo=30001323`) × IMAX관(`scnsNo=018`) 회차 시각까지 담아 알림을 보낸다.

## 설치 (10분)

### 1. 폰에 ntfy 앱 설치하고 토픽 정하기

1. [ntfy](https://ntfy.sh/) 앱 설치 — [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)
2. 앱에서 **+** → 토픽 이름 입력 후 구독

> **토픽 이름은 곧 비밀번호다.** ntfy.sh는 가입이 없어서, 토픽 이름을 아는 사람은 누구나
> 그 토픽에 알림을 보낼 수 있다. `cgv-yongsan` 같은 짐작 가능한 이름 말고
> `cgv-yongsan-imax-x7k2m9qf` 처럼 임의 문자열을 섞어라.

### 2. GitHub 저장소 만들고 올리기

```bash
cd cgv-imax-alert
git init
git add .
git commit -m "feat: CGV 용산 IMAX 예매 오픈 감시기"
gh repo create cgv-imax-alert --public --source=. --push
```

> 저장소를 **공개**로 두면 Actions 사용량이 무제한 무료다. 비공개면 월 2,000분 한도가 있는데
> 이 워크플로는 5분마다 ~5분씩 도니까 한도를 금방 넘긴다. 토픽 이름은 Secret에 들어가므로
> 공개 저장소여도 코드에는 노출되지 않는다.

### 3. 토픽을 Secret에 등록

```bash
gh secret set NTFY_TOPIC
# 프롬프트에 1번에서 정한 토픽 이름을 붙여넣는다
```

또는 웹에서: **Settings → Secrets and variables → Actions → New repository secret**,
이름 `NTFY_TOPIC`.

### 4. 알림이 오는지 테스트

```bash
gh workflow run watch.yml -f test_notify=true
```

폰에 "🔔 CGV 감시기 테스트"가 뜨면 설정 완료다. 이제 8/19 예매가 열리는 순간
자동으로 알림이 온다.

## 알림 예시

```
🎬 오디세이 용산 IMAX 예매 오픈!

CGV 용산아이파크몰 예매가 새로 열렸습니다.

• 2026-08-19 — IMAX 6회차: 06:30, 10:00, 13:30, 17:00, 20:30, 24:00

지금 바로 예매하세요 →
```

알림을 누르면 CGV 용산 예매 페이지로 바로 이동한다.

## 로컬에서 확인해보기

```bash
# 실제 전송 없이 지금 상태만 확인
DRY_RUN=1 python check.py

# 이미 열린 날짜로 감지 로직 테스트
DRY_RUN=1 WATCH_FROM=2026-08-17 STATE_PATH=/tmp/s.json python check.py
```

## 설정 바꾸기

`.github/workflows/watch.yml`의 `env:` 블록만 고치면 된다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| `WATCH_FROM` | `2026-08-19` | 이 날짜(포함) 이후가 열리면 알림 |
| `PLACE_ID` | `12298207` | 네이버 플레이스 ID (CGV 용산아이파크몰) |
| `MOVIE_NO` | `30001323` | CGV 영화코드 (오디세이) |
| `MOVIE_NAME` | `오디세이` | 알림에 표시할 영화 이름 |
| `SCREEN_NO` | `018` | 상영관 번호 (용산 IMAX관) |
| `SCREEN_NAME` | `IMAX` | 알림에 표시할 상영관 이름 |
| `POLL_SECONDS` | `270` | 한 번 실행될 때 폴링을 유지할 시간(초) |
| `POLL_INTERVAL` | `30` | 폴링 간격(초) |

다른 극장/영화를 감시하려면 네이버에서 극장을 검색해 `place/<숫자>` 를 `PLACE_ID`로,
상영시간표의 예매 링크에 있는 `movNo`·`scnsNo` 를 각각 옮겨 적으면 된다.

## 동작 관련 메모

- **중복 알림 방지** — 이미 알린 날짜는 `state.json`에 기록되고, 워크플로가 저장소로 커밋한다.
  다시 알림을 받고 싶으면 `state.json`의 `notified_dates`를 비우면 된다.
- **크론 지연** — GitHub 크론은 부하가 몰리면 5~15분 밀릴 수 있다. 크론 자체를 앞당길 수는
  없지만, 한 번 실행될 때 계속 폴링하도록 만들어서 지연 영향을 줄였다.
- **감시기 고장 감지** — 네이버 페이지 구조가 바뀌어 날짜를 못 읽으면 "⚠️ 감시기 점검 필요"
  알림이 하루 한 번 온다. 조용히 죽지 않는다.
- **일시적 오류** — 네트워크 오류는 무시하고 다음 폴링에서 재시도한다.

## 예매가 끝나면

```bash
gh workflow disable watch.yml
```
