# CGV 용산아이파크몰 IMAX 예매 오픈 알림

`오디세이` 8/19 이후 회차 예매가 열리는 순간 폰으로 푸시를 보낸다.

- **컴퓨터가 꺼져 있어도 동작** — GitHub Actions(클라우드)에서 돈다.
- **LLM 토큰 0** — 순수 파이썬 스크립트.
- **비용 0** — 공개 저장소면 Actions 무료, ntfy.sh도 무료.
- **감시 주기 약 30초** — 크론은 5분마다 뜨지만, 한 번 뜰 때마다 4분 30초 동안 30초 간격으로 확인한다.
- **이중 소스** — CGV 공식 API와 네이버 플레이스를 각각 확인하고, **둘 중 하나라도** 새 날짜를 발견하면 알린다.

## 어떻게 감지하나

### 1차 — CGV 공식 API

```
https://cgv.co.kr/api/v1/booking/searchSiteScnscYmdListByMov
    ?coCd=A420&siteNo=0013&movNo=30001323
```

오디세이가 용산에서 **예매 가능한 날짜 목록**을 그대로 준다. 여기에 `20260819`가 나타나는 순간이 예매 오픈이다.
그 뒤 `searchSchByMov`로 회차별 시각과 **잔여 좌석 수**까지 받아 알림에 담는다.

접근에 두 가지 함정이 있었다:

- **`www` 를 빼야 한다.** `www.cgv.co.kr/api/...` 는 봇 차단으로 SPA 껍데기(HTML)만 주고,
  `www` 없는 아펙스 `cgv.co.kr/api/...` 만 실제 JSON을 준다.
- **TLS 지문을 검사한다.** Cloudflare가 일반 HTTP 클라이언트를 403으로 막는다.
  `curl_cffi`의 Chrome 임퍼소네이션이 필요하다. (헤드리스 크로미움은 별도로 차단된다 —
  *"비정상적으로 CGV에 접속한 것이 확인되어 이용이 제한되었어요"*)

`coCd`(회사코드)는 `A420`, 용산 `siteNo`는 `0013`, IMAX관 `scnsNo`는 `018`이다.

### 2차 — 네이버 플레이스

```
https://m.place.naver.com/place/12298207/movie?datefilter=YYYY-MM-DD
```

같은 상영시간표를 서버 렌더링으로 내려주고 봇 차단이 없다. 표준 라이브러리만으로 접근된다.
CGV 쪽이 막히거나 API 스펙이 바뀌어도 감시가 끊기지 않도록 하는 백업이다.

> 두 소스는 완전히 독립적이다. 한쪽이 죽으면 다른 쪽으로 계속 감시하고, **둘 다** 죽었을 때만
> "⚠️ 감시기 점검 필요" 경고가 하루 한 번 온다. 조용히 죽지 않는다.

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
git init && git add . && git commit -m "feat: CGV 용산 IMAX 예매 오픈 감시기"
gh repo create cgv-imax-alert --public --source=. --push
```

> 저장소를 **공개**로 두면 Actions 사용량이 무제한 무료다. 비공개면 월 2,000분 한도가 있는데
> 이 워크플로는 5분마다 ~5분씩 도니까 한도를 금방 넘긴다. 토픽 이름은 Secret에 들어가므로
> 공개 저장소여도 코드에는 노출되지 않는다.

### 3. 토픽을 Secret에 등록

**Settings → Secrets and variables → Actions → New repository secret**, 이름 `NTFY_TOPIC`.
초록색 **Add secret** 버튼까지 눌러야 저장된다.

터미널이 편하면 (값이 화면에 안 보이고 대화 기록에도 안 남는다):

```bash
gh secret set NTFY_TOPIC
```

### 4. 알림이 오는지 테스트

```bash
gh workflow run watch.yml -f test_notify=true
```

폰에 "🔔 CGV 감시기 테스트"가 뜨면 설정 완료다.

## 알림 예시

```
🎬 오디세이 용산 IMAX 예매 오픈!

CGV 용산아이파크몰 예매가 새로 열렸습니다.

• 2026-08-19  [CGV/네이버]
  IMAX 6회차
  06:30 (잔여 32/624석)
  10:00 (잔여 3/624석)
  13:30 (잔여 9/624석)
  17:00 (잔여 11/624석)
  20:30 (잔여 20/624석)
  24:00 (잔여 65/624석)

지금 바로 예매하세요 →
```

`[CGV/네이버]`는 어느 소스가 잡았는지다. 잔여 좌석은 CGV API로 잡혔을 때만 나온다.
알림을 누르면 CGV 용산 예매 페이지로 바로 이동한다.

## 로컬에서 확인해보기

```bash
pip install curl_cffi          # 없어도 네이버 소스만으로 동작한다

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
| `MOVIE_NO` | `30001323` | CGV 영화코드 (오디세이) |
| `MOVIE_NAME` | `오디세이` | 알림에 표시할 영화 이름 |
| `SITE_NO` | `0013` | CGV 극장코드 (용산아이파크몰) |
| `SCREEN_NO` | `018` | 상영관 번호 (용산 IMAX관) |
| `SCREEN_NAME` | `IMAX` | 알림에 표시할 상영관 이름 |
| `SCREEN_GRADE` | `아이맥스` | 특별관 등급명 (`SCREEN_NO` 백업 판정) |
| `CO_CD` | `A420` | CGV 회사코드 |
| `PLACE_ID` | `12298207` | 네이버 플레이스 ID |
| `POLL_SECONDS` | `270` | 한 번 실행될 때 폴링을 유지할 시간(초) |
| `POLL_INTERVAL` | `30` | 폴링 간격(초) |

다른 극장/영화를 감시하려면 네이버 상영시간표의 예매 링크에 들어 있는
`movNo`·`siteNo`·`scnsNo`를 그대로 옮겨 적고, `PLACE_ID`는 네이버에서 극장을 검색해
URL의 `place/<숫자>`를 쓰면 된다.

## 동작 관련 메모

- **중복 알림 방지** — 이미 알린 날짜는 `state.json`에 기록되고, 워크플로가 저장소로 커밋한다.
  다시 알림을 받고 싶으면 `notified_dates`를 비우면 된다.
- **크론 지연** — GitHub 크론은 부하가 몰리면 5~15분 밀릴 수 있다. 크론 자체를 앞당길 수는
  없지만, 한 번 실행될 때 계속 폴링하도록 만들어서 지연 영향을 줄였다.
- **소스 상태** — `state.json`의 `source_status`에 각 소스의 마지막 상태가 남는다.
- **빈 응답은 실패로 다룬다** — CGV가 "예매 가능 날짜 0건"을 주면 정상이 아니라고 보고
  실패 처리한다. 조용히 넘기면 코드가 바뀐 걸 못 잡기 때문이다.

## 예매가 끝나면

```bash
gh workflow disable watch.yml
```
