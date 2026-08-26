"""Google Gemini(무료 티어)로 뉴스를 채널 포맷 JSON으로 변환.

기존 Claude 버전과 인터페이스 동일: summarize(item) -> dict | None.
Gemini SDK는 동기라 asyncio.to_thread 로 감싸 async 인터페이스를 유지한다.

무료 티어는 503(과부하)이 잦으므로 지수 백오프 재시도 + 대체 모델 폴백을 둔다.
모델이 JSON 앞뒤에 설명을 붙이는 경우가 있어 본문에서 JSON 객체만 추출한다.
"""
import asyncio
import json
import re

from google import genai
from google.genai import types

from config import settings
from models import NewsItem
from prompts import (
    INSIGHT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_insight_prompt,
    build_user_prompt,
)

# 타임아웃을 주지 않으면 응답이 끊겨도 영원히 대기한다.
# 실제로 백필이 이 상태로 7시간 멈춰 있었다. (단위: ms)
client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=90_000),
)

# 라이브러리 단 타임아웃이 안 먹는 경우를 대비한 상한(초).
CALL_TIMEOUT = 120

# 무료 티어는 모델당 분당 15건이다. 넘기면 429 로 실패하고 그 항목은 유실된다.
# 여유를 두고 12건/분으로 스스로 제한한다.
RATE_LIMIT_RPM = 12
_MIN_GAP = 60.0 / RATE_LIMIT_RPM
_gate = asyncio.Lock()
_last_call = 0.0


async def _throttle():
    """호출 간격을 벌려 분당 한도를 넘지 않게 한다."""
    global _last_call
    async with _gate:
        now = asyncio.get_running_loop().time()
        wait = _MIN_GAP - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = asyncio.get_running_loop().time()


# 일일 한도를 소진한 모델. 한 번 걸리면 그 실행 동안 다시 시도하지 않는다.
# 이걸 안 하면 죽은 모델에 재시도를 반복해 실행이 수십 분씩 멈춘다.
_exhausted: set[str] = set()


def _is_quota_exhausted(msg: str) -> bool:
    """분당 초과(잠시 후 회복)와 한도 소진(그날은 끝)을 구분한다.

    분당 한도 메시지에도 'PerMinutePerProject...' 처럼 Per 가 들어가므로
    PerDay 를 정확히 집어야 한다. 잘못 판정하면 살아 있는 모델을 배제해버린다.
    """
    if "RESOURCE_EXHAUSTED" not in msg:
        return False
    return "PerDay" in msg and "PerMinute" not in msg


def _candidates() -> list[str]:
    """시도할 모델 목록. 주 모델이 폴백 목록에도 있으면 중복 호출이 되므로 정리한다."""
    return list(dict.fromkeys([settings.gemini_model, *FALLBACK_MODELS]))


def _usable(models: list[str]) -> list[str]:
    """아직 살아 있는 모델만. 전부 소진이면 빈 목록을 준다.

    예전에는 전부 소진일 때 목록을 그대로 돌려줘 '어쩔 수 없이 다시 시도'했는데,
    그러면 건마다 모델 수만큼 429 를 받아낸다. _throttle() 이 전역이라 호출 하나당
    5초가 붙어, 소진된 날에는 100여 건 처리에 40분이 걸려 job 타임아웃에 잘렸다.
    살아 있는 모델이 없으면 그냥 포기하는 게 맞다.
    """
    return [m for m in models if m not in _exhausted]


def all_exhausted() -> bool:
    """이번 실행에서 쓸 수 있는 모델이 하나도 안 남았는가.

    호출하는 쪽이 남은 항목을 '요약 실패'가 아니라 '손대지 않음'으로 처리해
    다음 실행에 넘길 수 있게 하려고 공개해 둔다.
    """
    return not _usable(_candidates())


def _retry_after(msg: str) -> float:
    """429 응답에 담긴 대기 시간을 뽑아낸다. 없으면 기본값."""
    m = re.search(r"retry in ([0-9.]+)s", msg) or re.search(r"'retryDelay': '(\d+)s'", msg)
    if m:
        try:
            return min(float(m.group(1)) + 2, 90)
        except ValueError:
            pass
    return 20.0

# 기본 모델이 과부하(503)·소진일 때 순서대로 시도할 대체 모델.
#
# 2026-08-26 실측 — 무료 티어 일일 한도(429 응답의 quotaValue)는 모델마다 다르다:
#   gemini-3.1-flash-lite   500건/일   ← 유일하게 상시 운영이 가능한 창구
#   gemini-3.7-flash         20건/일
#   gemini-3.5-flash         20건/일
#   gemini-3-flash-preview   20건/일
# 그래서 주 모델(GEMINI_MODEL)은 flash-lite 로 두고, 나머지는 소진 후 예비로만 쓴다.
# 이 순서를 되돌리면 하루 20건 만에 발행이 멈춘다.
FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-3.7-flash",
                   "gemini-3.5-flash", "gemini-3-flash-preview"]

_config = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    response_mime_type="application/json",
    temperature=0.7,
    max_output_tokens=1500,
)

# 트위터 캡처용. 배경·영향·지켜볼 포인트까지 쓰므로 출력 토큰을 넉넉히 준다.
# 사실 왜곡을 줄이려 temperature 를 낮춘다.
_insight_config = types.GenerateContentConfig(
    system_instruction=INSIGHT_SYSTEM_PROMPT,
    response_mime_type="application/json",
    temperature=0.4,
    max_output_tokens=3000,
)


def _retryable(err: Exception, msg: str) -> bool:
    """같은 모델로 다시 시도해볼 만한 오류인가.

    타임아웃은 응답이 늦은 것뿐이라 재시도 대상이다. 이걸 빠뜨리면
    한 번 느려졌을 때 곧장 대체 모델로 넘어가버린다.
    """
    if isinstance(err, (TimeoutError, asyncio.TimeoutError)):
        return True
    return ("503" in msg or "429" in msg or "UNAVAILABLE" in msg
            or "RESOURCE_EXHAUSTED" in msg)


def _extract_json(text: str) -> dict:
    """모델이 앞뒤에 설명을 붙여도 JSON 객체만 뽑아 파싱한다."""
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _generate_sync(model: str, user_prompt: str) -> str:
    resp = client.models.generate_content(
        model=model, contents=user_prompt, config=_config
    )
    return resp.text or ""


def _generate_vision_sync(model: str, user_prompt: str, image: bytes | None,
                          mime: str) -> str:
    contents = [user_prompt] if image is None else [
        types.Part.from_bytes(data=image, mime_type=mime),
        user_prompt,
    ]
    resp = client.models.generate_content(
        model=model, contents=contents, config=_insight_config
    )
    return resp.text or ""


async def generate_json(system_prompt: str, user_prompt: str,
                        max_tokens: int = 2000) -> dict | None:
    """임의의 시스템 프롬프트로 JSON 응답을 받는다(다이제스트 등 범용).

    요약·분류 경로와 동일하게 503/429 재시도 + 대체 모델 폴백을 적용한다.
    """
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        temperature=0.4,
        max_output_tokens=max_tokens,
    )

    def _call(model: str) -> str:
        resp = client.models.generate_content(
            model=model, contents=user_prompt, config=config
        )
        return resp.text or ""

    models = _usable(_candidates())
    if not models:
        return None                      # 오늘 쓸 수 있는 모델 없음 — 헛호출하지 않는다
    last_err = None
    for model in models:
        for attempt in range(3):
            try:
                await _throttle()
                return _extract_json(
                    await asyncio.wait_for(asyncio.to_thread(_call, model), CALL_TIMEOUT)
                )
            except Exception as e:
                last_err = e
                msg = str(e)
                if _is_quota_exhausted(msg):
                    # 그날 한도가 끝난 모델이다. 기다려도 안 되니 바로 다음 모델로.
                    if model not in _exhausted:
                        _exhausted.add(model)
                        print(f"[모델] {model} 일일 한도 소진 — 이번 실행에서 제외")
                    break
                if _retryable(e, msg):
                    delay = _retry_after(msg) if "429" in msg or "RESOURCE_EXHAUSTED" in msg \
                        else 2 * (attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                break

    print(f"[generate_json] 실패: {last_err}")
    return None


async def summarize_insight(item: NewsItem, posted_at: str) -> dict | None:
    """퍼온 글을 분석 인사이트 JSON으로 만든다.

    이미지가 있으면 비전으로 캡처를 읽고, 없으면 본문 텍스트만으로 처리한다.
    **이미지가 없다고 일반 뉴스 경로로 흘려보내면 안 된다** — 그 경로는 출처를
    '기사 원문 = 수집처 링크'로 표기해서, 퍼온 채널이 출처로 찍혀버린다.
    """
    user_prompt = build_insight_prompt(item.body, item.url, posted_at,
                                       has_image=bool(item.image))
    models = _usable(_candidates())
    if not models:
        return None                      # 오늘 쓸 수 있는 모델 없음 — 헛호출하지 않는다
    last_err = None

    for model in models:
        for attempt in range(3):
            try:
                await _throttle()
                text = await asyncio.wait_for(
                    asyncio.to_thread(_generate_vision_sync, model, user_prompt,
                                      item.image, item.image_mime),
                    CALL_TIMEOUT,
                )
                data = _extract_json(text)
                if not data.get("relevant"):
                    return None
                data["_insight"] = True
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                if _is_quota_exhausted(msg):
                    # 그날 한도가 끝난 모델이다. 기다려도 안 되니 바로 다음 모델로.
                    if model not in _exhausted:
                        _exhausted.add(model)
                        print(f"[모델] {model} 일일 한도 소진 — 이번 실행에서 제외")
                    break
                if _retryable(e, msg):
                    delay = _retry_after(msg) if "429" in msg or "RESOURCE_EXHAUSTED" in msg \
                        else 2 * (attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                break

    print(f"[insight] 실패 ({item.url}): {last_err}")
    return None


async def summarize(item: NewsItem) -> dict | None:
    """실패하거나 관련 없는 뉴스면 None 반환."""
    user_prompt = build_user_prompt(
        item.source, item.title, item.url, item.body, item.region_hint
    )
    models = _usable(_candidates())
    if not models:
        return None                      # 오늘 쓸 수 있는 모델 없음 — 헛호출하지 않는다
    last_err = None

    for model in models:
        for attempt in range(3):
            try:
                await _throttle()
                text = await asyncio.wait_for(
                    asyncio.to_thread(_generate_sync, model, user_prompt), CALL_TIMEOUT
                )
                data = _extract_json(text)
                if not data.get("relevant"):
                    return None
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                # 과부하/레이트리밋이면 잠시 쉬었다 재시도, 그 외 오류는 다음 모델로
                if _is_quota_exhausted(msg):
                    # 그날 한도가 끝난 모델이다. 기다려도 안 되니 바로 다음 모델로.
                    if model not in _exhausted:
                        _exhausted.add(model)
                        print(f"[모델] {model} 일일 한도 소진 — 이번 실행에서 제외")
                    break
                if _retryable(e, msg):
                    delay = _retry_after(msg) if "429" in msg or "RESOURCE_EXHAUSTED" in msg \
                        else 2 * (attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                break  # 파싱 실패 등 → 다음 모델 시도

    print(f"[summarizer] 실패 ({item.title[:40]}): {last_err}")
    return None
