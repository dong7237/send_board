# 대학원 공지 메일 알림 (GitHub Actions)
* 본 코드는 한양대학교 경영대학 공지사항을 읽어오는 것으로 한정됩니다.

매일 한국시간 오전 9:00에 공지사항을 확인합니다.
새 글이 있으면 메일로 알려줍니다.

- 공지 URL: `https://bizgrad.hanyang.ac.kr/nt1`
- 받는 메일: GitHub Secret로 설정
- 첫 실행: 메일 안 보냄 (state만 저장)
## 준비물

- GitHub 계정 (무료 플랜)
- 보내는 사람 네이버 메일 계정 1개

## 사용 방법

### 1) GitHub 저장소에 올리기

이 폴더 파일들을 GitHub 저장소에 올립니다.

### 2) GitHub Secrets 설정하기

GitHub 저장소에서 아래로 들어갑니다.

- `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

그리고 3개를 추가합니다.

- `NOTICE_EMAIL_TO`: 받는 메일 주소 (예: `someone@example.com`)
- `NAVER_SMTP_USER`: 보내는 네이버 메일 주소 (예: `myid@naver.com`)
- `NAVER_SMTP_PASS`: 네이버 `앱 비밀번호(메일)` (일반 로그인 비밀번호 불가)

네이버에서 메일 전송이 안 되면, 네이버 메일 설정에서 `POP3/IMAP/SMTP 사용`을 켜야 합니다.
이 저장소 기본 SMTP 설정은 `smtp.naver.com:587` + `STARTTLS`입니다.

### 3) 한번 실행해보기 (테스트)

- GitHub `Actions` 탭
- `Check Graduate Notices` 워크플로 선택
- `Run workflow` 클릭

첫 실행은 메일을 보내지 않고 `state.json`만 저장합니다.

### 4) 매일 자동 실행

`.github/workflows/check_notices.yml`에 아래가 들어 있습니다.

- `0 0 * * *` (UTC) = 매일 한국시간 09:00

## 자주 바꾸는 것

- 받는 메일을 바꾸고 싶으면: Secret `NOTICE_EMAIL_TO` 값 수정
- 더 많은 글을 보고 싶으면: 워크플로에 `PAGES` 환경변수를 추가해서 숫자를 올리기
