from __future__ import annotations

import logging
import subprocess
import threading
from typing import List


logger = logging.getLogger("speech")


class SpeechService:
    def __init__(self, enabled: bool = True, rate: int = 0, volume: int = 100) -> None:
        self.enabled = enabled
        self.rate = rate
        self.volume = volume
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.current_engine = None
        self.current_process = None
        self.threads: List[threading.Thread] = []
        logger.info("Initializing PC speech service: enabled=%s rate=%s volume=%s", enabled, rate, volume)

    def say(self, text: str, wait: bool = False) -> None:
        if not self.enabled or not text:
            return
        thread = threading.Thread(target=self._say_blocking, args=(text,), name="pc-speech")
        thread.daemon = True
        self.threads.append(thread)
        thread.start()
        if wait:
            thread.join()

    def wait_for_idle(self) -> None:
        for thread in list(self.threads):
            if thread.is_alive():
                thread.join()
            try:
                self.threads.remove(thread)
            except ValueError:
                pass

    def is_busy(self) -> bool:
        self.threads = [thread for thread in self.threads if thread.is_alive()]
        return any(thread.is_alive() for thread in self.threads)

    def stop_all(self) -> None:
        with self.state_lock:
            engine = self.current_engine
            process = self.current_process
        if engine:
            try:
                engine.stop()
            except Exception as exc:
                logger.debug("Could not stop pyttsx3 speech: %s", exc)
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError as exc:
                logger.debug("Could not terminate Windows SAPI speech: %s", exc)

    def _say_blocking(self, text: str) -> None:
        with self.lock:
            logger.info("Speaking through PC speakers: %s", text)
            if self._say_with_pyttsx3(text):
                return
            self._say_with_windows_sapi(text)

    def _say_with_pyttsx3(self, text: str) -> bool:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self._pyttsx3_rate())
            engine.setProperty("volume", max(0.0, min(float(self.volume) / 100.0, 1.0)))
            engine.say(text)
            with self.state_lock:
                self.current_engine = engine
            try:
                engine.runAndWait()
            finally:
                engine.stop()
                with self.state_lock:
                    if self.current_engine is engine:
                        self.current_engine = None
            return True
        except Exception as exc:
            logger.warning("pyttsx3 speech failed; falling back to Windows SAPI. Error: %s", exc)
            return False

    def _pyttsx3_rate(self) -> int:
        # pyttsx3 uses words per minute; Windows SAPI uses roughly -10..10.
        return max(80, min(260, 180 + int(self.rate) * 12))

    def _say_with_windows_sapi(self, text: str) -> None:
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$speaker.Rate = %s; "
            "$speaker.Volume = %s; "
            "$speaker.Speak($args[0]); "
            "$speaker.Dispose();"
        ) % (self.rate, self.volume)
        try:
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script, text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self.state_lock:
                self.current_process = process
            process.wait()
            with self.state_lock:
                if self.current_process is process:
                    self.current_process = None
        except OSError as exc:
            logger.warning("Windows SAPI speech failed: %s", exc)
