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

client = genai.Client(api_key=settings.gemini_api_key)

# 기본 모델이 과부하(503)일 때 순서대로 시도할 대체 모델
FALLBACK_MODELS = ["gemini-3-flash-preview", "gemini-3.1-flash-lite"]

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


async def summarize_insight(item: NewsItem, posted_at: str) -> dict | None:
    """퍼온 글을 분석 인사이트 JSON으로 만든다.

    이미지가 있으면 비전으로 캡처를 읽고, 없으면 본문 텍스트만으로 처리한다.
    **이미지가 없다고 일반 뉴스 경로로 흘려보내면 안 된다** — 그 경로는 출처를
    '기사 원문 = 수집처 링크'로 표기해서, 퍼온 채널이 출처로 찍혀버린다.
    """
    user_prompt = build_insight_prompt(item.body, item.url, posted_at,
                                       has_image=bool(item.image))
    models = [settings.gemini_model, *FALLBACK_MODELS]
    last_err = None

    for model in models:
        for attempt in range(3):
            try:
                text = await asyncio.to_thread(
                    _generate_vision_sync, model, user_prompt, item.image, item.image_mime
                )
                data = _extract_json(text)
                if not data.get("relevant"):
                    return None
                data["_insight"] = True
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                if "503" in msg or "429" in msg or "UNAVAILABLE" in msg:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break

    print(f"[insight] 실패 ({item.url}): {last_err}")
    return None


async def summarize(item: NewsItem) -> dict | None:
    """실패하거나 관련 없는 뉴스면 None 반환."""
    user_prompt = build_user_prompt(
        item.source, item.title, item.url, item.body, item.region_hint
    )
    models = [settings.gemini_model, *FALLBACK_MODELS]
    last_err = None

    for model in models:
        for attempt in range(3):
            try:
                text = await asyncio.to_thread(_generate_sync, model, user_prompt)
                data = _extract_json(text)
                if not data.get("relevant"):
                    return None
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                # 과부하/레이트리밋이면 잠시 쉬었다 재시도, 그 외 오류는 다음 모델로
                if "503" in msg or "429" in msg or "UNAVAILABLE" in msg:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break  # 파싱 실패 등 → 다음 모델 시도

    print(f"[summarizer] 실패 ({item.title[:40]}): {last_err}")
    return None
