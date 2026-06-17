"""Voice support: ElevenLabs text-to-speech plus a short-lived audio cache.

Inbound calls reuse the same agent pipeline as SMS. Twilio transcribes the
caller's speech; this module turns the agent's text reply into spoken audio
(ElevenLabs) and holds the bytes briefly so Twilio can fetch them with <Play>.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict

import httpx

from swinedesk.settings import settings

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Audio is fetched by Twilio moments after we mint the URL, so we only need to
# keep a small, short-lived buffer. Oldest entries are evicted past the cap.
_AUDIO_CACHE_MAX = 128
_AUDIO_TTL_SECONDS = 600

# id -> (mp3_bytes, created_at)
_audio_cache: "OrderedDict[str, tuple[bytes, float]]" = OrderedDict()

# Static phrases (greeting, fallbacks) reuse the same synthesized bytes across
# calls instead of hitting ElevenLabs every time. Keyed by the phrase text.
_phrase_cache: dict[str, bytes] = {}


def voice_available() -> bool:
    """True when voice replies can be synthesized."""
    return bool(settings.voice_enabled and settings.elevenlabs_api_key)


async def synthesize_speech(text: str) -> bytes | None:
    """Render text to mp3 via ElevenLabs. Returns None if unavailable or failed."""
    if not voice_available() or not text.strip():
        return None

    url = ELEVENLABS_TTS_URL.format(voice_id=settings.elevenlabs_voice_id)
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": settings.elevenlabs_model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                params={"output_format": "mp3_44100_128"},
            )
            response.raise_for_status()
            return response.content
    except Exception:
        logger.exception("ElevenLabs TTS synthesis failed")
        return None


def _evict_stale(now: float) -> None:
    while _audio_cache:
        oldest_id, (_, created_at) = next(iter(_audio_cache.items()))
        if len(_audio_cache) > _AUDIO_CACHE_MAX or (now - created_at) > _AUDIO_TTL_SECONDS:
            _audio_cache.popitem(last=False)
        else:
            break


def store_audio(data: bytes) -> str:
    """Cache mp3 bytes and return an id for the playback URL."""
    now = time.time()
    audio_id = uuid.uuid4().hex
    _audio_cache[audio_id] = (data, now)
    _audio_cache.move_to_end(audio_id)
    _evict_stale(now)
    return audio_id


def get_audio(audio_id: str) -> bytes | None:
    """Fetch cached mp3 bytes by id, or None if missing/expired."""
    entry = _audio_cache.get(audio_id)
    if entry is None:
        return None
    return entry[0]


async def synthesize_and_store(text: str) -> str | None:
    """Synthesize text and cache it. Returns the audio id, or None on failure."""
    data = await synthesize_speech(text)
    if data is None:
        return None
    return store_audio(data)


async def synthesize_phrase_and_store(text: str) -> str | None:
    """Like synthesize_and_store, but reuses bytes for repeated static phrases."""
    cached = _phrase_cache.get(text)
    if cached is None:
        cached = await synthesize_speech(text)
        if cached is None:
            return None
        _phrase_cache[text] = cached
    return store_audio(cached)
