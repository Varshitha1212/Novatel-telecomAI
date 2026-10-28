import asyncio
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles 
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel, Field

from app.langchain_agent import (
    LangChainNotConfiguredError,
    TelecomIntegrationError,
    answer_question,
)
from app.vector_store import ensure_knowledge_base_ingested

load_dotenv()

app = FastAPI(title="Telecom Support Chat")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://novatel-telecom-ai.vercel.app",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend"
logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/mp4": "dictation.mp4",
    "audio/mpeg": "dictation.mp3",
    "audio/mpga": "dictation.mpga",
    "audio/ogg": "dictation.ogg",
    "audio/wav": "dictation.wav",
    "audio/webm": "dictation.webm",
    "audio/x-m4a": "dictation.m4a",
    "audio/x-wav": "dictation.wav",
}


def _chat_provider_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) or bool(os.getenv("GROQ_API_KEY"))


@app.on_event("startup")
async def ingest_knowledge_base_if_empty() -> None:
    await asyncio.to_thread(ensure_knowledge_base_ingested)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict[str, bool | str]:
    livekit_configured = all(
        os.getenv(name)
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    )
    agent_configured = _chat_provider_configured()
    return {
        "chat_enabled": agent_configured,
        "voice_enabled": livekit_configured and agent_configured,
        "dictation_enabled": bool(os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")),
        "support_contact": os.getenv("SUPPORT_CONTACT", ""),
    }


@app.post("/api/transcribe")
async def transcribe_audio(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    filename = SUPPORTED_AUDIO_TYPES.get(content_type)
    if filename is None:
        raise HTTPException(
            status_code=415,
            detail="Unsupported audio format. Record a WebM, MP4, OGG, WAV, or MP3 clip.",
        )

    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="No recorded audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That recording is too large. Please record a shorter message.",
        )

    groq_api_key = os.getenv("GROQ_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if groq_api_key:
        api_key = groq_api_key
        model = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
        base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    elif openai_api_key:
        api_key = openai_api_key
        model = os.getenv("OPENAI_STT_MODEL", "whisper-1")
        base_url = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    else:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text is not configured. Add a Groq or OpenAI API key to .env.",
        )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model, "response_format": "json"},
                files={"file": (filename, audio, content_type)},
            )
    except httpx.RequestError as error:
        logger.exception("Speech transcription provider request failed")
        raise HTTPException(
            status_code=502,
            detail="Could not reach the speech-to-text service. Check your connection and try again.",
        ) from error

    if response.is_error:
        logger.error(
            "Speech transcription provider returned HTTP %s: %s",
            response.status_code,
            response.text[:1000],
        )
        if response.status_code in (401, 403):
            detail = "The speech-to-text provider rejected the API key. Check the key in .env."
        elif response.status_code == 429:
            detail = "The speech-to-text provider is rate-limited or out of quota. Try again later."
        elif response.status_code == 400:
            detail = "The speech service could not process this recording. Try recording a short message again."
        else:
            detail = "The speech-to-text service returned an error. Please try again."
        raise HTTPException(status_code=502, detail=detail)

    try:
        transcript = response.json().get("text", "").strip()
    except (ValueError, AttributeError) as error:
        logger.exception("Speech transcription provider returned invalid JSON")
        raise HTTPException(
            status_code=502,
            detail="The speech-to-text service returned an unreadable response.",
        ) from error
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="No speech was detected. Check your microphone and try speaking again.",
        )
    return {"text": transcript}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, str | None]:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Please enter a question.")

    try:
        answer, source = await answer_question(question)
    except (LangChainNotConfiguredError, TelecomIntegrationError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"answer": answer, "source": source}


@app.post("/api/livekit/token")
async def livekit_token() -> dict[str, str]:
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    if not livekit_url or not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="Voice support is not configured. Add the LiveKit credentials to .env.",
        )
    if not _chat_provider_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "LangChain support is not configured. Set OPENAI_API_KEY or "
                "GROQ_API_KEY in .env."
            ),
        )

    room_name = f"support-{uuid.uuid4().hex}"
    identity = f"guest-{uuid.uuid4().hex[:12]}"
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Guest")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )

    async with api.LiveKitAPI() as livekit_api:
        await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="telecom-support",
                room=room_name,
            )
        )

    return {"url": livekit_url, "token": token}


app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")
