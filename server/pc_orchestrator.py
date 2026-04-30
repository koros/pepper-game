from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from audio_service import AudioService, UtteranceBuffer
from cup_game_logic import CupGame
from lm_studio_client import LMStudioClient
from sockets import listen, local_ip, recv_blob, recv_image, recv_json_line, send_json
from speech_service import SpeechService
from vision_service import VisionService


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
logger = logging.getLogger("orchestrator")


class CupGameOrchestrator:
    def __init__(self, host: str, ports: Dict[str, int], flags: Dict[str, object]) -> None:
        self.host = host
        self.ports = ports
        self.flags = flags
        self.running = True
        self.command_conn = None
        self.result_conn = None
        self.result_lock = threading.Lock()
        self.pepper_vision_check_requested = False
        self.pepper_vision_check_lock = threading.Lock()
        self.last_pepper_preview_log_at = 0.0
        self.awaiting_control = False
        self.awaiting_replay_response = False

        logger.info("Initializing cup game orchestrator")
        logger.info("Host bind address: %s", host)
        logger.info("Socket ports: %s", ports)
        logger.info("Runtime flags: %s", flags)

        self.game = CupGame(
            target_sequence=self.load_string_list(
                flags.get("target_sequence", ["red", "blue", "orange", "yellow"])
            ),
            allowed_colors=self.load_string_list(
                flags.get("allowed_colors", ["red", "blue", "orange", "yellow", "green", "purple"])
            ),
            max_attempts=int(flags.get("max_attempts", 15)),
        )
        self.audio = AudioService(
            whisper_model=str(flags["whisper_model"]),
            vad_threshold=float(flags["energy_vad_threshold"]),
        )
        self.audio_buffer = UtteranceBuffer(
            vad_threshold=float(flags["energy_vad_threshold"]),
            min_speech_frames=int(flags["energy_vad_min_speech_frames"]),
            end_silence_frames=int(flags["energy_vad_end_silence_frames"]),
        )
        self.vision = VisionService(
            pc_camera_index=int(flags["pc_camera_index"]),
            preview_enabled=bool(flags.get("vision_preview_enabled", True)),
            live_preview_enabled=(
                bool(flags.get("vision_live_preview_enabled", False))
                and str(flags.get("vision_input_mode", "pc")) == "pc"
            ),
            live_analysis_interval=float(flags.get("vision_live_analysis_interval", 2.0)),
            save_dir=str(ROOT / str(flags.get("vision_save_dir", "captured_frames"))),
            window_name=str(flags.get("vision_window_name", "Pepper Cup Game Vision")),
        )
        self.llm = LMStudioClient(
            base_url=str(flags["lm_studio_base_url"]),
            model=str(flags["lm_studio_model"]),
        )
        self.speech = SpeechService(
            enabled=bool(flags.get("pc_speech_enabled", True)),
            rate=int(flags.get("pc_speech_rate", 0)),
            volume=int(flags.get("pc_speech_volume", 100)),
        )

    def start(self) -> None:
        logger.info("PC IP for Pepper: %s", local_ip())
        logger.info("Command port: %s", self.ports["command_port"])
        logger.info("Audio mode: %s", self.flags["audio_input_mode"])
        logger.info("Vision mode: %s", self.flags["vision_input_mode"])
        logger.info("Speech mode: %s", self.flags["speech_output_mode"])

        threads = [
            threading.Thread(target=self.command_loop, name="command-loop"),
            threading.Thread(target=self.audio_loop, name="audio-loop"),
            threading.Thread(target=self.vision_loop, name="vision-loop"),
            threading.Thread(target=self.result_loop, name="result-loop"),
        ]
        for thread in threads:
            thread.daemon = True
            thread.start()
            logger.info("Started background thread: %s", thread.name)

        while self.running:
            time.sleep(0.25)

    def command_loop(self) -> None:
        server = listen(self.host, self.ports["command_port"])
        logger.info("Command socket listening on port %s", self.ports["command_port"])
        while self.running:
            conn, addr = server.accept()
            self.command_conn = conn
            logger.info("Command client connected from %s:%s", *addr)
            with conn:
                while self.running:
                    message = recv_json_line(conn)
                    if message is None:
                        logger.info("Command client disconnected")
                        break
                    self.handle_command(message)
            self.command_conn = None

    def audio_loop(self) -> None:
        server = listen(self.host, self.ports["audio_port"])
        logger.info("Audio socket listening on port %s", self.ports["audio_port"])
        while self.running:
            conn, addr = server.accept()
            logger.info("Audio client connected from %s:%s", *addr)
            with conn:
                while self.running:
                    chunk = recv_blob(conn)
                    if chunk is None:
                        logger.info("Audio client disconnected")
                        break
                    utterance = self.audio_buffer.consume(chunk)
                    if utterance:
                        logger.info("Completed Pepper audio utterance: %s bytes", len(utterance))
                        self.handle_audio_utterance(utterance)

    def vision_loop(self) -> None:
        server = listen(self.host, self.ports["vision_port"])
        logger.info("Vision socket listening on port %s", self.ports["vision_port"])
        while self.running:
            conn, addr = server.accept()
            logger.debug("Vision client connected from %s:%s", *addr)
            with conn:
                frame = recv_image(conn)
                if frame is None:
                    logger.warning("Vision client disconnected before sending a full frame")
                    continue
                width, height, data = frame
                if self.consume_pepper_vision_check_request():
                    logger.info(
                        "Received Pepper vision check frame: width=%s height=%s bytes=%s",
                        width,
                        height,
                        len(data),
                    )
                    result = self.vision.check_pepper_frame(width, height, data)
                    self.handle_vision_result(result)
                else:
                    self.log_pepper_preview_frame(width, height, len(data))
                    self.vision.preview_pepper_frame(width, height, data)

    def result_loop(self) -> None:
        server = listen(self.host, self.ports["result_port"])
        logger.info("Result socket listening on port %s", self.ports["result_port"])
        while self.running:
            conn, addr = server.accept()
            logger.info("Result client connected from %s:%s", *addr)
            self.result_conn = conn
            while self.running:
                time.sleep(0.25)
                if self.result_conn is None:
                    logger.info("Result client connection cleared")
                    break

    def handle_command(self, message: Dict[str, object]) -> None:
        event = str(message.get("event", ""))
        logger.info("Command received: event=%s payload=%s", event, message)
        if event == "hello":
            self.send_result("ready", "PC cup game server is ready.")
        elif event == "start_round":
            self.start_round()
        elif event == "pc_audio_turn":
            self.handle_pc_audio_turn()
        elif event == "pc_vision_check":
            self.handle_pc_vision_check()
        elif event == "pepper_vision_check":
            self.request_pepper_vision_check()
            self.send_result("vision_requested", "Checking the Pepper camera frame.")
        elif event == "user_guess":
            self.handle_guess(str(message.get("text", "")))
        else:
            logger.warning("Unknown command event: %s", event)
            self.send_result("unknown_event", "Unknown command: %s" % event)

    def handle_audio_utterance(self, pcm: bytes) -> None:
        if self.flags["audio_input_mode"] != "pepper":
            logger.info("Ignoring Pepper audio because audio_input_mode=%s", self.flags["audio_input_mode"])
            return
        logger.info("Transcribing Pepper audio utterance")
        text = self.audio.transcribe_pcm(pcm)
        if text:
            logger.info("Transcribed Pepper audio as: %s", text)
            self.handle_spoken_input(text)
        else:
            logger.info("Pepper audio utterance had no recognized speech")
            self.say("I did not catch that. Please say the cup colors from left to right, or say help.")

    def handle_pc_audio_turn(self) -> None:
        if self.flags["audio_input_mode"] != "pc":
            logger.warning("PC audio requested while audio_input_mode=%s", self.flags["audio_input_mode"])
            self.send_result(
                "mode_ignored",
                "PC audio was requested, but audio_input_mode is not pc.",
            )
            return
        logger.info("Starting PC microphone capture turn")
        self.speech.wait_for_idle()
        self.say("Listening from the PC microphone.", event="pc_audio_started", wait=True)
        text = self.audio.capture_and_transcribe_pc_mic()
        if text:
            logger.info("Transcribed PC microphone as: %s", text)
            self.handle_spoken_input(text)
        else:
            logger.info("PC microphone capture produced no recognized speech")
            self.say("I did not catch that from the PC microphone.", wait=True)
            if self.awaiting_replay_response:
                self.prompt_for_replay(listen=True)
            else:
                self.prompt_for_control(listen=True)

    def handle_pc_vision_check(self) -> None:
        if self.flags["vision_input_mode"] != "pc":
            logger.warning("PC vision requested while vision_input_mode=%s", self.flags["vision_input_mode"])
            self.send_result(
                "mode_ignored",
                "PC vision was requested, but vision_input_mode is not pc.",
            )
            return
        logger.info("Capturing PC camera frame for cup check")
        result = self.vision.check_pc_camera()
        self.handle_vision_result(result)

    def handle_vision_result(self, result: Dict[str, object]) -> None:
        if self.flags["vision_input_mode"] not in ("pepper", "pc"):
            logger.error("Invalid vision_input_mode=%s", self.flags["vision_input_mode"])
            self.send_result("config_error", "Invalid vision_input_mode.")
            return
        top_row = result.get("top_row", result.get("sequence", []))
        bottom_row = result.get("bottom_row", [])
        logger.info("Vision result: top_row=%s bottom_row=%s raw=%s", top_row, bottom_row, result)
        if isinstance(bottom_row, list) and bottom_row:
            self.game.set_target_sequence([str(color) for color in bottom_row])
        if isinstance(top_row, list) and top_row:
            outcome = self.game.evaluate_sequence([str(color) for color in top_row])
            outcome["top_row"] = top_row
            outcome["bottom_row_detected"] = bottom_row
        else:
            outcome = {
                "status": "needs_sequence",
                "reply": "I cannot read the top row clearly. Please arrange the colored cups in the upper row.",
                "vision": result,
            }
        logger.info("Vision sequence outcome: %s", outcome)
        self.say(str(outcome["reply"]), event="vision_checked", data=outcome)
        if not bool(outcome.get("finished", False)):
            self.prompt_for_control(listen=True)
        else:
            self.prompt_for_replay(listen=True)

    def handle_guess(self, text: str) -> None:
        logger.info("Checking user guess text: %s", text)
        outcome = self.game.check_guess(text)
        logger.info("Guess outcome: %s", outcome)
        self.say(outcome["reply"], event="guess_checked", data=outcome)
        if not bool(outcome.get("finished", False)):
            self.prompt_for_control(listen=True)
        else:
            self.prompt_for_replay(listen=True)

    def handle_spoken_input(self, text: str) -> None:
        if self.awaiting_replay_response:
            self.handle_replay_response(text)
            return

        command = self.game.classify_command(text)
        logger.info("Spoken input classified as %s: %s", command, text)
        if command == "check":
            if self.is_dev_speech_sequence_mode():
                self.say("Please say the current top row colors from left to right.", wait=True)
                self.prompt_for_control(listen=True)
                return
            if self.flags["vision_input_mode"] == "pc":
                self.handle_pc_vision_check()
            elif self.flags["vision_input_mode"] == "pepper":
                self.request_pepper_vision_check()
                self.send_result("vision_requested", "Checking the Pepper camera frame.")
            else:
                self.send_result("config_error", "Invalid vision_input_mode.")
        elif command == "hint":
            outcome = self.game.hint()
            logger.info("Hint outcome: %s", outcome)
            self.say(str(outcome["reply"]), event="hint", data=outcome)
            self.prompt_for_control(listen=True)
        else:
            self.handle_guess(text)

    def start_round(self, shuffle_target: bool = False) -> None:
        self.awaiting_replay_response = False
        if shuffle_target:
            self.game.shuffle_target_sequence()
        round_state = self.game.start_round()
        logger.info(
            "Started sequence game: target_length=%s max_attempts=%s",
            round_state["target_length"],
            round_state["max_attempts"],
        )
        self.say(round_state["instruction"], event="round_started", data=round_state)
        self.prompt_for_control(listen=True)

    def prompt_for_replay(self, listen: bool = False) -> None:
        self.awaiting_replay_response = True
        self.say("Do you want to play again? Say yes or no.", event="play_again_prompt", wait=listen)
        if listen and self.flags["audio_input_mode"] == "pc":
            self.handle_pc_audio_turn()

    def handle_replay_response(self, text: str) -> None:
        lowered = text.lower()
        if re.search(r"\b(yes|yeah|yep|sure|again|play again|restart)\b", lowered):
            logger.info("Replay accepted by user: %s", text)
            self.say("Great. I shuffled the hidden row. Starting a new game.", event="play_again", wait=True)
            self.start_round(shuffle_target=True)
            return
        if re.search(r"\b(no|nope|stop|not now|quit)\b", lowered):
            logger.info("Replay declined by user: %s", text)
            self.awaiting_replay_response = False
            self.say("Okay. The game is finished.", event="play_again")
            return
        logger.info("Replay response unclear: %s", text)
        self.say("Please say yes to play again, or no to finish.", event="play_again_prompt", wait=True)
        if self.flags["audio_input_mode"] == "pc":
            self.handle_pc_audio_turn()

    def prompt_for_control(self, listen: bool = False) -> None:
        if self.flags["audio_input_mode"] == "pc" and not self.game.finished:
            logger.info("Ready for user control word: ready, check, or help")
            prompt = (
                "Please say the current top row colors from left to right. Say help if you need a clue."
                if self.is_dev_speech_sequence_mode()
                else "Say ready or check when you want me to evaluate the top row. Say help if you need a clue."
            )
            self.say(
                prompt,
                wait=listen,
            )
            if listen:
                self.handle_pc_audio_turn()

    def is_dev_speech_sequence_mode(self) -> bool:
        return bool(self.flags.get("dev_speech_sequence_mode", False))

    def request_pepper_vision_check(self) -> None:
        with self.pepper_vision_check_lock:
            self.pepper_vision_check_requested = True
        logger.info("Pepper vision check armed; next Pepper frame will be evaluated")

    def consume_pepper_vision_check_request(self) -> bool:
        with self.pepper_vision_check_lock:
            if not self.pepper_vision_check_requested:
                return False
            self.pepper_vision_check_requested = False
            return True

    def log_pepper_preview_frame(self, width: int, height: int, byte_count: int) -> None:
        now = time.time()
        if now - self.last_pepper_preview_log_at < 5.0:
            return
        self.last_pepper_preview_log_at = now
        logger.info(
            "Receiving Pepper vision preview frames: latest width=%s height=%s bytes=%s",
            width,
            height,
            byte_count,
        )

    def say(
        self,
        message: str,
        event: str = "say",
        data: Optional[Dict[str, object]] = None,
        wait: bool = False,
    ) -> None:
        logger.info("Preparing Pepper speech event=%s message=%s", event, message)
        polished = message if self.should_use_literal_speech(event) else self.llm.polish_for_pepper(message)
        if polished != message:
            logger.info("LM Studio polished message: %s", polished)
        if self.flags["speech_output_mode"] == "pc":
            logger.info("[PC SPEECH] %s", polished)
            self.speech.say(polished, wait=wait)
        self.send_result(event, polished, data)

    def should_use_literal_speech(self, event: str) -> bool:
        return event in {
            "pc_audio_started",
            "ready",
            "round_started",
            "say",
            "guess_checked",
            "vision_checked",
            "hint",
            "play_again",
            "play_again_prompt",
            "stopped",
            "mode_ignored",
            "config_error",
        }

    def send_result(
        self,
        event: str,
        text: str,
        data: Optional[Dict[str, object]] = None,
    ) -> None:
        payload = {"event": event, "text": text, "data": data or {}}
        with self.result_lock:
            if self.result_conn:
                try:
                    send_json(self.result_conn, payload)
                    logger.info("Sent result over result socket: event=%s", event)
                    return
                except OSError:
                    logger.warning("Result socket send failed; clearing result connection")
                    self.result_conn = None
            if self.command_conn:
                try:
                    send_json(self.command_conn, payload)
                    logger.info("Sent result over command socket fallback: event=%s", event)
                    return
                except OSError:
                    logger.warning("Command socket fallback send failed; clearing command connection")
                    self.command_conn = None
        logger.warning("No Pepper result socket available for payload: %s", payload)

    def load_string_list(self, value: object) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return []


def load_json(path: Path) -> Dict[str, object]:
    logger.info("Loading JSON config: %s", path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    logger.info("Loaded JSON config: %s", data)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Pepper cup game PC server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--ports", default=str(CONFIG_DIR / "ports.json"))
    parser.add_argument("--flags", default=str(CONFIG_DIR / "runtime_flags.json"))
    args = parser.parse_args()

    ports = load_json(Path(args.ports))
    flags = load_json(Path(args.flags))
    orchestrator = CupGameOrchestrator(args.host, ports, flags)
    orchestrator.start()


if __name__ == "__main__":
    main()
