from __future__ import annotations

import os
import logging
import re
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * SAMPLE_WIDTH
logger = logging.getLogger("audio")


class EnergyVad:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        logger.info("Energy VAD initialized with threshold=%s", threshold)

    def is_speech(self, frame: bytes) -> bool:
        if not frame:
            return False
        samples = np.frombuffer(frame, dtype=np.int16)
        if samples.size == 0:
            return False
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        return rms >= self.threshold


class AudioService:
    def __init__(
        self,
        whisper_model: str,
        vad_threshold: float,
        min_speech_frames: int = 3,
        end_silence_frames: int = 18,
    ) -> None:
        logger.info(
            "Initializing audio service: whisper_model=%s energy_vad_threshold=%s min_speech_frames=%s end_silence_frames=%s",
            whisper_model,
            vad_threshold,
            min_speech_frames,
            end_silence_frames,
        )
        self.vad = EnergyVad(vad_threshold)
        self.min_speech_frames = min_speech_frames
        self.end_silence_frames = end_silence_frames
        logger.info("Loading faster-whisper model. This can take a moment on first run.")
        self.whisper = WhisperModel(whisper_model, device="cpu", compute_type="int8")
        logger.info("Audio service ready")

    def has_speech(self, pcm: bytes) -> bool:
        logger.info("Running VAD over %s bytes of PCM audio", len(pcm))
        speech_frames = 0
        total_frames = 0
        for start in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
            frame = pcm[start : start + FRAME_BYTES]
            total_frames += 1
            if self.vad.is_speech(frame):
                speech_frames += 1
        has_speech = total_frames > 0 and speech_frames >= 3
        logger.info(
            "VAD summary: total_frames=%s speech_frames=%s has_speech=%s",
            total_frames,
            speech_frames,
            has_speech,
        )
        return has_speech

    def write_wav(self, pcm: bytes) -> str:
        temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp.close()
        with wave.open(temp.name, "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        logger.info("Temporary WAV written for transcription: %s", temp.name)
        return temp.name

    def transcribe_pcm(self, pcm: bytes) -> str:
        if not self.has_speech(pcm):
            logger.warning("PC microphone VAD found no speech to transcribe")
            return ""
        wav_path = self.write_wav(pcm)
        try:
            logger.info("Starting faster-whisper transcription")
            segments, _info = self.whisper.transcribe(wav_path, beam_size=5)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if text and not re.search(r"[A-Za-z0-9]", text):
                logger.warning("Discarding punctuation-only transcription: %s", text)
                text = ""
            logger.info("Whisper transcription from microphone: %s", text or "[empty]")
            return text
        finally:
            try:
                os.remove(wav_path)
                logger.info("Removed temporary WAV: %s", wav_path)
            except OSError:
                logger.warning("Could not remove temporary WAV: %s", wav_path)
                pass

    def capture_and_transcribe_pc_mic(
        self,
        seconds: float = 7.0,
        on_speech_start: Optional[Callable[[], bool]] = None,
    ) -> str:
        return self.listen_with_streaming_whisper(seconds, on_speech_start=on_speech_start)

    def listen_with_streaming_whisper(
        self,
        max_seconds: float = 30.0,
        on_speech_start: Optional[Callable[[], bool]] = None,
        accept_transcript: Optional[Callable[[str], bool]] = None,
        poll_key: Optional[Callable[[], Optional[str]]] = None,
        block_seconds: float = 0.5,
        blocks_per_transcription: int = 6,
    ) -> str:
        logger.info(
            "Listening with callback microphone stream for up to %.1f seconds",
            max_seconds,
        )
        block_frames = int(SAMPLE_RATE * block_seconds)
        deadline = time.monotonic() + max_seconds
        audio_buffer = []
        rolling_audio = []
        buffer_lock = threading.Lock()
        prompt_reset_done = False
        activity_threshold = self.vad.threshold / 32768.0
        max_rolling_blocks = max(int(12.0 / block_seconds), blocks_per_transcription)
        last_transcript = ""

        def audio_callback(indata, frames, time_state, status):
            if status:
                logger.warning("PC microphone callback status: %s", status)
            with buffer_lock:
                audio_buffer.append(indata.copy())
                rolling_audio.append(indata.copy())
                if len(rolling_audio) > max_rolling_blocks:
                    del rolling_audio[: len(rolling_audio) - max_rolling_blocks]

        def transcribe_array(audio: np.ndarray) -> str:
            segments, _info = self.whisper.transcribe(audio, beam_size=5)
            return " ".join(segment.text.strip() for segment in segments).strip()

        def accepted_text(text: str) -> bool:
            return bool(
                text
                and len(text) > 2
                and re.search(r"[A-Za-z0-9]", text)
                and (accept_transcript is None or accept_transcript(text))
            )

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=block_frames,
            callback=audio_callback,
        ):
            while time.monotonic() < deadline:
                if poll_key:
                    key_text = poll_key()
                    if key_text:
                        logger.info("PC keyboard override received while listening: %s", key_text)
                        return key_text

                with buffer_lock:
                    buffered_blocks = len(audio_buffer)
                if buffered_blocks < blocks_per_transcription:
                    time.sleep(0.05)
                    continue

                with buffer_lock:
                    recent_blocks = list(audio_buffer)
                    rolling_blocks = list(rolling_audio)
                    audio_buffer.clear()

                recent_audio = np.concatenate(recent_blocks, axis=0).flatten()
                rolling_full_audio = np.concatenate(rolling_blocks, axis=0).flatten()
                rms = float(np.sqrt(np.mean(recent_audio.astype(np.float32) ** 2)))

                if not prompt_reset_done and on_speech_start and rms >= activity_threshold:
                    prompt_reset_done = bool(on_speech_start())
                    if prompt_reset_done:
                        logger.info(
                            "Stopped active prompt after PC microphone activity: rms=%.4f",
                            rms,
                        )
                        with buffer_lock:
                            audio_buffer.clear()
                            rolling_audio.clear()
                        continue

                text = transcribe_array(recent_audio)
                if accepted_text(text):
                    return text
                if text and text != last_transcript:
                    last_transcript = text
                    logger.debug("PC mic transcript was not accepted as current input: %s", text)

                if len(rolling_blocks) > blocks_per_transcription:
                    rolling_text = transcribe_array(rolling_full_audio)
                    if accepted_text(rolling_text):
                        return rolling_text
                    if rolling_text and rolling_text != last_transcript:
                        last_transcript = rolling_text
                        logger.debug(
                            "PC mic rolling transcript was not accepted as current input: %s",
                            rolling_text,
                        )

        logger.info("PC microphone listener timed out without recognized speech")
        return ""


class UtteranceBuffer:
    def __init__(
        self,
        vad_threshold: float,
        min_speech_frames: int,
        end_silence_frames: int,
    ) -> None:
        logger.info(
            "Initializing utterance buffer with threshold=%s min_speech_frames=%s end_silence_frames=%s",
            vad_threshold,
            min_speech_frames,
            end_silence_frames,
        )
        self.vad = EnergyVad(vad_threshold)
        self.min_speech_frames = min_speech_frames
        self.end_silence_frames = end_silence_frames
        self.active = False
        self.speech_frames = 0
        self.silence_frames = 0
        self.pending = bytearray()
        self.utterance = bytearray()

    def consume(self, data: bytes) -> Optional[bytes]:
        self.pending.extend(data)
        completed = None

        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            is_speech = self.vad.is_speech(frame)

            if is_speech:
                self.speech_frames += 1
                self.silence_frames = 0
            else:
                self.silence_frames += 1

            if not self.active and self.speech_frames >= self.min_speech_frames:
                self.active = True
                self.utterance = bytearray()
                logger.info("Speech start detected in Pepper audio stream")

            if self.active:
                self.utterance.extend(frame)

            if self.active and self.silence_frames >= self.end_silence_frames:
                completed = bytes(self.utterance)
                logger.info("Speech end detected in Pepper audio stream: %s bytes", len(completed))
                self.active = False
                self.speech_frames = 0
                self.silence_frames = 0
                self.utterance = bytearray()
                break

            if not self.active and not is_speech:
                self.speech_frames = 0

        return completed
