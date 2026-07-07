# 갤지기 GallKeeper

디시인사이드 마이너갤러리 운영자를 위한 미러링 및 관리 자동화 봇입니다.

## 용도

`갤지기`는 관리가 어려운 갤러리의 주제 글 흐름을 관리 가능한 마이너 갤러리로 안내하거나 미러링하고, 매니저 권한이 있는 마이너 갤러리에서 악성 글/댓글을 감시하기 위한 운영 보조 봇이다.

이 저장소의 기본 예시는 재즈갤러리와 즉흥연주갤러리를 기준으로 한다. 다른 마이너 갤러리 운영자는 이 예시를 복사한 뒤, 원래 이용자가 모여 있던 갤러리와 직접 운영하는 마이너 갤러리에 맞게 `board_id`, `board_name`, 키워드, 운영 보조 규칙을 바꿔서 사용한다.

운영 형태는 여러 source 갤러리의 주제 글을 한 topic gallery로 모으는 구성을 지원한다. 예를 들어 포락갤, 일마갤, 엘피갤에서 재즈 관련 글을 감시하고, 발견된 글을 재즈갤러리로 미러링할 수 있다. managed minor gallery는 봇글로 오염시키지 않고, 직접 운영하는 갤러리 안의 악성 글/댓글 감시와 운영 보조를 담당한다.

## 구현 사항
1. [x] 주기적으로 지정된 게시판 읽기
   1. [x] 글 목록은 모바일 기준 1페이지 이내로 읽기 권고.
   2. [x] 읽기 후 가장 최근 작성된 글 번호 저장
2. [x] 지정된 갤러리에 모바일 기준 1페이지 이내 (8개)에 봇글이 없는 경우, 지정된 글 게시
   1. [x] 봇글 존재 여부는 지정된 작성자 존재 여부로 파악한다.
   * (봇 전용 닉 지정 권고)
   2. [x] 봇글의 타이틀과 작성자, 게시 내용, 비밀번호는 저장된 설정 파일을 이용.
   3. [x] 봇글이 없는 경우 봇글 게시.
3. [x] 지정된 갤러리에 지정된 키워드가 포함된 글이 올라올 시 지정된 댓글을 남김
   * 예: 포락갤에 문자열 '재즈'가 포함된 글이 게시될 경우 봇이 즉흥갤 링크 댓글 작성
   1. [x] 해당 글에 봇 전용 닉의 댓글 존재 여부 확인 후, 댓글이 없는 경우 즉시 댓글 작성.
   2. [x] 댓글의 작성자, 게시 내용, 비밀번호는 저장된 설정 파일을 이용.
   3. [x] 봇의 게시글은 제외
4. [ ] 지정된 갤러리에 지정된 키워드가 올라오면 지정된 갤러리에 원글 출처와 함께 미러링
   * 예: 포락갤에 문자열 '재즈'가 포함된 글이 게시될 경우, 봇이 즉흥갤에 해당글을 포락갤 링크와 함께 게시
   1. [ ] 봇글의 작성자, 게시 내용은 원글을 따름
   2. [x] 봇글의 타이틀 앞에 출처 기록
   * 예: (재즈갤|80090) 재즈피아노 입문자, 마이너 스케일 관련 질문
   3. [x] 비밀번호는 저장된 설정 파일을 이용
   4. [x] 읽기 후 가장 최근 작성된 글 번호 저장
   5. [x] 전체 본문/HTML 미러링은 하지 않고 원본 갤러리명, 제목, 링크만 게시하는 정책으로 정리
   6. [ ] 봇의 게시글은 제외

## dependency

[dc_api fork]: https://github.com/dr-nitro-lab/dcinside-python3-api

- dc_api
  - [dc_api fork]
  - 현재 복구 작업에서는 PyPI 패키지가 아니라 NitroLab fork를 기준으로 사용한다.
  - 일반 설치는 GitHub 주소를 직접 지정해서 진행한다.

```bash
pip install git+https://github.com/dr-nitro-lab/dcinside-python3-api.git@master
```

## 운영 참고

운영용 설정 파일, 작성 내용 파일, 쿠키 파일, 캐시는 로컬 환경에만 둔다.

- `conf/*.local.yaml`
- `conf/*cookies*.json`
- `conf/*_contents.txt`
- `caches/`

공개 저장소에는 예시와 기본값만 둔다.

- `conf/default.yaml`: 단일 갤러리 설정 예시
- `conf/gall_conf_list.yaml`: 공개 기본 실행 목록
- `conf/*.example.yaml`: 로컬 설정을 만들 때 참고하는 예시
- `conf/dcinside_cookies.example.json`: 쿠키 파일 형식 예시

실제 운영에서는 로컬 설정 파일을 사용한다. 공개 예시의 값은 source 갤러리, 재즈갤러리, 즉흥연주갤러리를 기준으로 하되, 파일 역할은 source 갤러리와 관리 중인 마이너 갤러리로 구분한다.

- `conf/gall_conf_list.local.yaml`: 실행할 로컬 갤러리 설정 목록
- `conf/topic_gallery.local.yaml`: source 글을 모으는 topic gallery의 로컬 설정
- `conf/source_*.local.yaml`: 주제 글이 올라오는 여러 source 갤러리를 감시하고 topic gallery로 안내/미러링하기 위한 로컬 설정
- `conf/managed_minor_gallery.local.yaml`: 직접 운영하는 마이너 갤러리의 운영 보조와 moderation을 위한 로컬 설정. source 글 미러링 대상이 아니다.
- `conf/moderation_rules.local.yaml`: 로컬 운영 보조 규칙
- `.scratch/dcinside-session/cookies.json`: 브라우저에서 내보낸 로컬 쿠키 파일

`gallkeeper_main.py`는 `conf/gall_conf_list.local.yaml`이 있으면 이 파일을 먼저 사용하고, 없으면 `conf/gall_conf_list.yaml`을 사용한다.

`write_guard_file`은 글쓰기 안전 상태를 저장하는 sqlite 파일이다. 디시인사이드 글쓰기 응답에서 IP 차단, 비정상 접근, 도배 제한으로 보이는 팝업/alert 문구가 감지되면 원문 메시지와 정지 시간을 기록하고, 미러링/안내글/댓글/수정/삭제 같은 쓰기 작업을 일시 정지한다.

키워드 기반 규칙은 시행착오가 필요한 영역이므로 기본값은 `review`와 `auto_action: false`로 둔다. 자동 실행은 충분히 검증된 작성자 exact rule 또는 매우 확실한 금지 문구에만 제한적으로 켠다.

운영 보조 자동 실행은 기본적으로 꺼져 있다. 자동 실행을 쓰려면 갤러리 설정의 `moderation_auto_action`과 규칙의 `auto_action`을 모두 켜고, `moderation_auto_action_allow_galleries`에 대상 갤러리를 명시해야 한다.

실제 운영 전에 먼저 `--dry-run`으로 읽기와 판단 결과를 확인하는 것을 권장한다.

## 현재 복구 상태

확인된 기능:

- 게시판 읽기
- 글 본문 읽기
- 댓글 읽기
- PC 웹 경로를 이용한 글 작성
- PC 웹 경로를 이용한 글 수정
- 미러링 중복 방지를 위한 로컬 sqlite 캐시
- 원글 삭제 또는 수정 여부 확인 후 미러글 정리
- 글쓰기 차단 팝업/alert 감지 후 쓰기 작업 일시 정지

추가로 확인 중인 영역:

- 장기 실행 중 메모리 사용량
- 미러링 관련도 점수 모니터링
- 운영자용 로컬 보조 도구
- 인증 세션이 필요한 관리 기능의 안전한 분리

## 실행

쓰기 없이 한 번만 확인하려면:

```bash
python3 gallkeeper_main.py --dry-run --once --interval 0
```

설정 파일을 지정하려면:

```bash
python3 gallkeeper_main.py --config default.yaml --dry-run --once
```

실제 운영에서는 로컬 설정 파일을 사용한다. 공개 저장소에는 운영 중인 갤러리 설정이나 인증 정보를 포함하지 않는다.

## 라이선스

이 저장소의 라이선스는 [LICENSE](LICENSE)를 따른다.
