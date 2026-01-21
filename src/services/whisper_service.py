"""
Сервис для транскрибации аудио через OpenAI Whisper API.

Поддерживает:
- Извлечение аудио из видео (FFmpeg)
- Сжатие для уменьшения размера
- Разбивка на части для файлов > 25 МБ
- Транскрибация через Whisper API
"""

import os
import subprocess
import tempfile
import math
from pathlib import Path

from openai import OpenAI

from src.config import settings
from src.utils.logging import get_logger


logger = get_logger(__name__)

# Лимит Whisper API в байтах (25 МБ)
WHISPER_SIZE_LIMIT = 25 * 1024 * 1024

# Длина части в минутах для разбивки
CHUNK_MINUTES = 20


class WhisperService:
    """Сервис транскрибации аудио."""

    def __init__(self):
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        """Ленивая инициализация клиента."""
        if self._client is None:
            self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    def extract_audio(self, video_path: str, output_path: str) -> bool:
        """
        Извлекает аудио из видеофайла и сжимает.

        Args:
            video_path: Путь к видеофайлу
            output_path: Путь для сохранения аудио

        Returns:
            bool: Успешность операции
        """
        try:
            # Сжимаем в моно, 16kHz, 32kbps — оптимально для Whisper
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", video_path,
                "-vn",  # Без видео
                "-ac", "1",  # Моно
                "-ar", "16000",  # 16kHz
                "-b:a", "32k",  # 32kbps
                "-f", "mp3",
                output_path
            ], capture_output=True, text=True, timeout=600)

            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                return False

            return True
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timeout (>10 min)")
            return False
        except Exception as e:
            logger.error(f"Error extracting audio: {e}")
            return False

    def get_audio_duration(self, audio_path: str) -> float:
        """Получает длительность аудио в секундах."""
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path
            ], capture_output=True, text=True, timeout=30)

            return float(result.stdout.strip())
        except Exception as e:
            logger.error(f"Error getting duration: {e}")
            return 0

    def split_audio(self, audio_path: str, chunk_minutes: int = CHUNK_MINUTES) -> list[str]:
        """
        Разбивает аудио на части.

        Args:
            audio_path: Путь к аудиофайлу
            chunk_minutes: Длина части в минутах

        Returns:
            list[str]: Список путей к частям
        """
        duration = self.get_audio_duration(audio_path)
        if duration == 0:
            return [audio_path]

        chunk_seconds = chunk_minutes * 60
        num_chunks = math.ceil(duration / chunk_seconds)

        if num_chunks == 1:
            return [audio_path]

        chunks = []
        temp_dir = tempfile.mkdtemp()

        for i in range(num_chunks):
            start = i * chunk_seconds
            output = os.path.join(temp_dir, f"chunk_{i}.mp3")

            try:
                subprocess.run([
                    "ffmpeg", "-y",
                    "-i", audio_path,
                    "-ss", str(start),
                    "-t", str(chunk_seconds),
                    "-ac", "1", "-ar", "16000", "-b:a", "32k",
                    output
                ], check=True, capture_output=True, timeout=120)
                chunks.append(output)
            except Exception as e:
                logger.error(f"Error splitting chunk {i}: {e}")

        return chunks if chunks else [audio_path]

    def transcribe_file(self, audio_path: str) -> str:
        """
        Транскрибирует один аудиофайл через Whisper API.

        Args:
            audio_path: Путь к аудиофайлу

        Returns:
            str: Текст транскрипции
        """
        try:
            with open(audio_path, "rb") as audio_file:
                response = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru",
                    response_format="text"
                )
            return response
        except Exception as e:
            logger.error(f"Whisper API error: {e}")
            return ""

    async def transcribe(self, file_path: str, is_video: bool = False) -> str:
        """
        Полный процесс транскрибации файла.

        Args:
            file_path: Путь к файлу (видео или аудио)
            is_video: True если это видеофайл

        Returns:
            str: Полный текст транскрипции
        """
        temp_files = []

        try:
            # Если видео — извлекаем аудио
            if is_video:
                audio_path = file_path.rsplit(".", 1)[0] + "_audio.mp3"
                temp_files.append(audio_path)

                logger.info(f"Extracting audio from {file_path}")
                if not self.extract_audio(file_path, audio_path):
                    return ""
            else:
                audio_path = file_path

            # Проверяем размер
            file_size = os.path.getsize(audio_path)
            logger.info(f"Audio size: {file_size / 1024 / 1024:.1f} MB")

            # Если больше лимита — разбиваем
            if file_size > WHISPER_SIZE_LIMIT:
                logger.info("File too large, splitting into chunks")
                chunks = self.split_audio(audio_path)
                temp_files.extend(chunks)
            else:
                chunks = [audio_path]

            # Транскрибируем каждую часть
            transcripts = []
            for i, chunk in enumerate(chunks):
                logger.info(f"Transcribing chunk {i+1}/{len(chunks)}")
                text = self.transcribe_file(chunk)
                if text:
                    transcripts.append(text)

            return "\n\n".join(transcripts)

        finally:
            # Очищаем временные файлы
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except Exception:
                    pass


# Глобальный экземпляр
whisper_service = WhisperService()
