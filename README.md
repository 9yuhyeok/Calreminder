# Calreminder

LearnUs iCalendar 일정을 불러와 완료 표시하는 앱입니다. 웹 버전은 구글 로그인으로 사용자별 일정과 완료 기록을 분리합니다. Chrome 확장 프로그램은 같은 계정의 일정을 팝업에서 보여 줍니다.

## 로컬 개인용 앱

macOS에서 `launch.command`를 더블클릭하면 기존 개인용 앱이 브라우저에서 열립니다. Python 3 외에 설치할 항목은 없습니다. 이 모드의 데이터는 `data/state.json`에 저장됩니다.

## 다중 사용자 웹 앱 설정

Python 3.12 기준입니다. 웹 앱은 Flask, Authlib, SQLAlchemy, PostgreSQL을 사용합니다.

1. Python 가상환경을 만들고 `pip install -r requirements.txt`를 실행합니다.
2. Google Cloud Console에서 OAuth 동의 화면을 설정하고 **웹 애플리케이션** OAuth 클라이언트를 만듭니다. 승인된 리디렉션 URI에 `https://YOUR-DOMAIN/auth/callback`을 등록합니다. 로컬 개발에는 `http://localhost:8000/auth/callback`을 등록합니다.
3. `.env.example`의 값을 환경 변수로 설정합니다. `.env` 파일과 Google 클라이언트 보안 비밀번호를 GitHub에 올리지 마세요.
4. `SESSION_SECRET`은 충분히 긴 임의 문자열로, `FEED_ENCRYPTION_KEY`는 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`로 생성합니다. 암호화 키를 바꾸면 기존 캘린더 링크를 읽을 수 없게 됩니다.
5. `python cloud.py`로 로컬에서 실행하거나, 운영 환경에서 `gunicorn cloud:app`으로 실행합니다.

필수 환경 변수:

| 이름 | 내용 |
| --- | --- |
| `APP_BASE_URL` | 공개 HTTPS 주소. 경로와 끝의 `/` 없이 입력 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth 웹 클라이언트 인증 정보 |
| `SESSION_SECRET` | 세션 서명 키 |
| `FEED_ENCRYPTION_KEY` | 피드 링크 암호화 키 |
| `DATABASE_URL` | PostgreSQL 연결 URL. 로컬 개발에는 `sqlite:///data/users.db` 가능 |
| `ALLOWED_FEED_HOSTS` | 허용할 Moodle 호스트. 기본값 `ys.learnus.org`, 쉼표로 추가 가능 |

구글 로그인에는 `openid email profile`만 요청합니다. 사용자는 로그인한 뒤 **본인의** LearnUs iCalendar 내보내기 링크를 입력해야 합니다. 이 링크의 인증 토큰은 데이터베이스에서 암호화해 저장하며 브라우저 API 응답에는 포함하지 않습니다. 일정과 완료 상태도 로그인한 사용자 ID로 분리됩니다.

## 공개 배포

GitHub 저장소에는 코드만 올립니다. GitHub Pages는 Python 서버를 실행할 수 없으므로 Flask 웹 서비스를 별도 호스팅해야 합니다. 예를 들어 Render에서 Web Service를 만들고 저장소를 연결한 뒤 build command를 `pip install -r requirements.txt`, start command를 `gunicorn cloud:app`으로 설정할 수 있습니다. Render와 같은 임시 파일 시스템에서는 PostgreSQL을 연결해 데이터가 배포 후에도 유지되도록 합니다. 환경 변수는 호스팅 서비스의 비밀 변수 설정에서 입력합니다.

Google OAuth 동의 화면을 외부 사용자용으로 구성하고, 실제 서비스 도메인을 승인된 도메인 및 리디렉션 URI에 등록하세요. 공개 전에 개인정보 처리방침과 운영자 연락처를 추가해야 할 수 있습니다.

## Chrome 확장 프로그램

1. 웹 앱을 HTTPS로 배포합니다.
2. Chrome의 `chrome://extensions`에서 개발자 모드를 켜고 **압축해제된 확장 프로그램을 로드합니다**를 눌러 `extension/` 폴더를 선택합니다.
3. 팝업은 `https://calreminder.onrender.com`에 기본 연결됩니다. 처음 연결할 때 해당 주소에 대한 접근 권한을 허용합니다.
4. 팝업에서 **Google로 로그인**을 누르고 새 탭에서 다시 **Google로 로그인**을 누릅니다. 인증을 마치면 탭이 닫히고, 팝업을 다시 열면 같은 계정의 일정이 나타납니다. 캘린더 링크 입력은 웹 앱에서 할 수 있습니다.

Chrome 웹 스토어 배포에는 개발자 계정 등록과 심사가 별도로 필요합니다.

## 개인 데이터

`data/`, `.env`, 데이터베이스, Google 인증 정보 파일은 `.gitignore`에 포함되어 있습니다. 특히 기존 개인용 앱의 `data/state.json`에는 사용자의 캘린더 인증 토큰과 일정이 있으므로 공유하거나 업로드하지 마세요.
