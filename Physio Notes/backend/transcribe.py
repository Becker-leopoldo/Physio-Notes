import os
import io
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
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
