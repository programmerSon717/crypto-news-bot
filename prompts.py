"""언어별 프롬프트 선택기.

봇은 한국어판과 영문판 두 벌이 같은 코드로 돈다. 언어에 묶인 것은 사실상
프롬프트·탭 이름·고정 문구뿐이라, 리포를 복사하는 대신 여기서 갈라 쓴다.
복사본을 두면 오늘 같은 수정(나이 제한·중요도 문턱·되돌리기)을 앞으로
영원히 두 벌씩 해야 한다.

    LOCALE=ko  (기본)  prompts_ko  — 한국어판. 지금 돌고 있는 그것
    LOCALE=en          prompts_en  — 영문판

**한국어판 동작은 이 파일이 생기기 전과 완전히 같아야 한다.**
prompts_ko.py 는 예전 prompts.py 를 이름만 바꾼 것이고 내용은 손대지 않았다.
tests/ko_snapshot.py 가 그것을 매번 대조한다.
"""
import os

LOCALE = os.getenv("LOCALE", "ko").lower()

if LOCALE == "en":
    from prompts_en import *          # noqa: F401,F403
    from prompts_en import (          # noqa: F401
        build_digest_prompt, build_insight_prompt, build_overview_prompt,
        build_purge_prompt, build_recent_block, build_reroute_prompt,
        build_user_prompt,
    )
else:
    from prompts_ko import *          # noqa: F401,F403
    from prompts_ko import (          # noqa: F401
        build_digest_prompt, build_insight_prompt, build_overview_prompt,
        build_purge_prompt, build_recent_block, build_reroute_prompt,
        build_user_prompt,
    )
