import os
import io
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Timeout explícito: evita que proxies (nginx/Render/Railway) abortem antes do Groq responder.
# Groq Whisper normalmente responde em < 10s para áudios de até 5 min.
_GROQ_TIMEOUT = float(os.getenv("GROQ_TIMEOUT_SECONDS", "55"))

client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    timeout=_GROQ_TIMEOUT,
    max_retries=0,  # não retentar — o frontend tem sua própria lógica de retry
)


async def transcrever_audio(audio_bytes: bytes, filename: str) -> str:
    """Envia áudio para Groq Whisper e retorna a transcrição em português."""
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    response = await client.audio.transcriptions.create(
        model="whisper-large-v3-turbo",
        file=audio_file,
        language="pt",
    )
    return response.text
