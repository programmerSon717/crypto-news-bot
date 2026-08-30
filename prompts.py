"""프롬프트 진입점.

내용은 prompts_ko.py 에 있다. 이 파일은 한때 언어 선택기였다 —
영문판을 같은 리포에서 돌리려던 흔적이다. 지금은 영문판이 별도 리포
(programmerSon717/crypto-news-bot-en)로 갈라져 나갔으므로 선택할 것이 없다.

**파일을 합치지 않고 남겨 둔 이유:** prompts_ko.py 는 예전 prompts.py 를
이름만 바꾼 것이고 내용이 바이트 단위로 같다. 합치면서 한 글자라도 어긋나면
채널에 나가는 글이 바뀐다. tests/ko_snapshot.py 가 그것을 대조하고 있다.
"""
from prompts_ko import *          # noqa: F401,F403
from prompts_ko import (          # noqa: F401
    build_digest_prompt, build_insight_prompt, build_overview_prompt,
    build_purge_prompt, build_recent_block, build_reroute_prompt,
    build_user_prompt,
)
