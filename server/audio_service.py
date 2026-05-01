from __future__ import annotations

import os
import logging
import re
import tempfile
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
            logger.warning("Whisper transcription from microphone: %s", text or "[empty]")
            return text
        finally:
            try:
                os.remove(wav_path)
                logger.info("Removed temporary WAV: %s", wav_path)
            except OSError:
                logger.warning("Could not remove temporary WAV: %s", wav_path)
                pass

    def capture_pc_mic(self, seconds: float = 7.0) -> bytes:
        logger.warning("Capturing PC microphone audio for %.1f seconds", seconds)
        frames = int(seconds * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        logger.warning("PC microphone capture complete: %s bytes", audio.nbytes)
        return audio.tobytes()

    def capture_pc_mic_until_silence(
        self,
        max_seconds: float = 7.0,
        on_speech_start: Optional[Callable[[], bool]] = None,
        reset_after_callback_seconds: float = 0.35,
        reset_extra_seconds: float = 4.0,
    ) -> bytes:
        logger.warning("Streaming PC microphone audio for up to %.1f seconds", max_seconds)
        block_frames = int(SAMPLE_RATE * FRAME_MS / 1000)
        deadline = time.monotonic() + max_seconds
        captured = bytearray()
        active = False
        speech_frames = 0
        silence_frames = 0
        ignore_until = 0.0

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=block_frames,
        ) as stream:
            while time.monotonic() < deadline:
                frame, overflowed = stream.read(block_frames)
                if overflowed:
                    logger.warning("PC microphone input overflowed while listening")

                frame_bytes = bytes(frame)
                if time.monotonic() < ignore_until:
                    continue

                captured.extend(frame_bytes)
                is_speech = self.vad.is_speech(frame_bytes)

                if is_speech:
                    speech_frames += 1
                    silence_frames = 0
                else:
                    silence_frames += 1

                if not active and speech_frames >= self.min_speech_frames:
                    active = True
                    logger.warning("Speech start detected in PC microphone stream")
                    if on_speech_start and on_speech_start():
                        logger.warning("Resetting PC microphone capture after stopping prompt echo")
                        captured = bytearray()
                        active = False
                        speech_frames = 0
                        silence_frames = 0
                        ignore_until = time.monotonic() + reset_after_callback_seconds
                        deadline = max(deadline, time.monotonic() + reset_extra_seconds)
                        continue

                if active and silence_frames >= self.end_silence_frames:
                    logger.warning(
                        "Speech end detected in PC microphone stream after %s bytes",
                        len(captured),
                    )
                    break

                if not active and not is_speech:
                    speech_frames = 0

        logger.warning("PC microphone streaming capture complete: %s bytes", len(captured))
        return bytes(captured)

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
        block_seconds: float = 0.5,
        blocks_per_transcription: int = 6,
    ) -> str:
        logger.warning(
            "Listening with callback microphone stream for up to %.1f seconds",
            max_seconds,
        )
        block_frames = int(SAMPLE_RATE * block_seconds)
        deadline = time.monotonic() + max_seconds
        audio_buffer = []
        prompt_reset_done = False
        activity_threshold = self.vad.threshold / 32768.0

        def audio_callback(indata, frames, time_state, status):
            if status:
                logger.warning("PC microphone callback status: %s", status)
            audio_buffer.append(indata.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=block_frames,
            callback=audio_callback,
        ):
            while time.monotonic() < deadline:
                if len(audio_buffer) < blocks_per_transcription:
                    time.sleep(0.05)
                    continue

                full_audio = np.concatenate(audio_buffer, axis=0).flatten()
                audio_buffer.clear()
                rms = float(np.sqrt(np.mean(full_audio.astype(np.float32) ** 2)))

                if not prompt_reset_done and on_speech_start and rms >= activity_threshold:
                    prompt_reset_done = bool(on_speech_start())
                    if prompt_reset_done:
                        logger.warning(
                            "Stopped active prompt after PC microphone activity: rms=%.4f",
                            rms,
                        )
                        continue

                segments, _info = self.whisper.transcribe(full_audio, beam_size=5)
                text = " ".join(segment.text.strip() for segment in segments).strip()
                logger.warning("PC mic transcript candidate: %s rms=%.4f", text or "[empty]", rms)

                if text and len(text) > 2 and re.search(r"[A-Za-z0-9]", text):
                    return text

        logger.warning("PC microphone listener timed out without recognized speech")
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
