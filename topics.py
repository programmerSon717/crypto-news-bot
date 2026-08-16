"""포럼 토픽(탭) 관리.

텔레그램의 Topics 는 슈퍼그룹 전용 기능이다. 채널(type=channel)에서는 쓸 수 없으므로
탭을 쓰려면 대상 채팅이 '주제(Topics)'가 켜진 그룹이어야 한다.

생성한 토픽의 message_thread_id 를 topics.json 에 캐시해 재실행 시 중복 생성을 막는다.
'All' 탭은 텔레그램이 자동으로 보여주므로 별도 생성하지 않는다.
"""
import json
import os

import httpx

from config import settings

API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# 카테고리 → (토픽 이름, 아이콘 색상)
# 색상은 텔레그램이 허용하는 6개 팔레트 중에서 지정한다(중복 사용 가능).
#   0x6FB9F0 파랑  0xFFD67E 노랑  0xCB86DB 보라
#   0x8EEE98 초록  0xFF93B2 분홍  0xFB6F5F 빨강
#
# 주요 관심국은 나라별 탭으로 분리하고, 그 외 국가·국제기구는 '해외정책'으로 모은다.
# 키(내부 식별자)는 모델이 분류값으로 뱉는 문자열이므로 탭 이름과 동일하게 유지한다.
CATEGORIES = {
    "국내정책": ("국내정책", 0x6FB9F0),
    "US Policy": ("US Policy", 0xFB6F5F),
    "Japan Policy": ("Japan Policy", 0xFF93B2),
    "Hong Kong Policy": ("Hong Kong Policy", 0xFB6F5F),
    "Singapore Policy": ("Singapore Policy", 0x8EEE98),
    "UAE Policy": ("UAE Policy", 0x8EEE98),
    "Vietnam Policy": ("Vietnam Policy", 0xFFD67E),
    "해외정책": ("해외정책", 0xFFD67E),
    "이슈": ("이슈", 0xCB86DB),
}


def _load() -> dict:
    if os.path.exists(settings.topics_file):
        with open(settings.topics_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(settings.topics_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def ensure_topics(client: httpx.AsyncClient) -> dict:
    """카테고리별 토픽을 생성(없을 때만)하고 {카테고리: thread_id} 를 반환."""
    cache = _load()
    for cat, (name, color) in CATEGORIES.items():
        if cat in cache:
            continue
        r = await client.post(
            f"{API}/createForumTopic",
            json={
                "chat_id": settings.telegram_channel_id,
                "name": name,
                "icon_color": color,
            },
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"토픽 생성 실패({name}): {data.get('description')}")
        cache[cat] = data["result"]["message_thread_id"]
        print(f"[topics] 생성됨: {name} (thread_id={cache[cat]})")
    _save(cache)
    return cache


def thread_id_for(category: str) -> int | None:
    """카테고리에 해당하는 thread_id. 토픽 미사용이면 None."""
    if not settings.use_topics:
        return None
    return _load().get(category)
