from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    import msvcrt
except ImportError:
    msvcrt = None

from audio_service import AudioService, UtteranceBuffer
from cup_game_logic import CupGame
from lm_studio_client import LMStudioClient
from sockets import listen, local_ip, recv_blob, recv_image, recv_json_line, send_json
from speech_service import SpeechService
from vision_service import VisionService

import sys

ROBOT_DIR = Path(__file__).resolve().parents[1] / "robot"
if str(ROBOT_DIR) not in sys.path:
    sys.path.append(str(ROBOT_DIR))

from pepper_pomdp import PepperPOMDP


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
        self.awaiting_control = False
        self.awaiting_replay_response = False
        self.awaiting_vision_target_shuffle = False
        self.previous_vision_target: Optional[List[str]] = None
        self.pc_mic_capture_seconds = float(flags.get("pc_mic_capture_seconds", 7.0))
        self.pc_audio_barge_in_enabled = bool(flags.get("pc_audio_barge_in_enabled", True))
        self.rearrange_pause_seconds = float(flags.get("rearrange_pause_seconds", 8.0))
        self.pc_audio_fallback_active = False
        self.keyboard_lock = threading.RLock()
        self.pepper_speech_condition = threading.Condition()
        self.next_pepper_speech_token = 0
        self.completed_pepper_speech_tokens: Set[str] = set()
        self.pepper_speech_timeout_seconds = float(flags.get("pepper_speech_done_timeout_seconds", 20.0))
        self.pomdp = PepperPOMDP()
        self.pomdp_hint_policy_enabled = bool(flags.get("pomdp_hint_policy_enabled", True))
        self.pomdp_fast_move_seconds = float(flags.get("pomdp_fast_move_seconds", 10.0))
        self.pomdp_min_attempts_before_offer = int(flags.get("pomdp_min_attempts_before_offer", 2))
        self.pomdp_offer_cooldown_attempts = int(flags.get("pomdp_offer_cooldown_attempts", 2))
        self.last_control_prompt_at = time.monotonic()
        self.last_hint_offer_attempt = 0
        self.hint_offer_pending = False

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
            min_speech_frames=int(flags["energy_vad_min_speech_frames"]),
            end_silence_frames=int(
                flags.get(
                    "pc_audio_end_silence_frames",
                    flags["energy_vad_end_silence_frames"],
                )
            ),
        )
        self.audio_buffer = UtteranceBuffer(
            vad_threshold=float(flags["energy_vad_threshold"]),
            min_speech_frames=int(flags["energy_vad_min_speech_frames"]),
            end_silence_frames=int(
                flags.get(
                    "pepper_audio_end_silence_frames",
                    flags["energy_vad_end_silence_frames"],
                )
            ),
        )
        self.vision = VisionService(
            pc_camera_index=int(flags["pc_camera_index"]),
            preview_enabled=bool(flags.get("vision_preview_enabled", True)),
            live_preview_enabled=(
                bool(flags.get("vision_live_preview_enabled", False))
                and str(flags.get("vision_input_mode", "pc")) == "pc"
            ),
            live_analysis_interval=float(flags.get("vision_live_analysis_interval", 2.0)),
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
                    self.start_vision_result_handler(result)
                else:
                    self.vision.preview_pepper_frame(width, height, data)

    def start_vision_result_handler(self, result: Dict[str, object]) -> None:
        # Keep the vision socket free to receive preview frames while speech/audio
        # feedback for a check result runs in the background.
        thread = threading.Thread(
            target=self.handle_vision_result,
            args=(result,),
            name="vision-result-handler",
        )
        thread.daemon = True
        thread.start()
        logger.info("Dispatched Pepper vision result handler; preview stream remains active")

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
        if event == "speech_done":
            self.handle_pepper_speech_done(message)
            return
        thread = threading.Thread(target=self.handle_command_event, args=(message,), name="command-%s" % event)
        thread.daemon = True
        thread.start()

    def handle_command_event(self, message: Dict[str, object]) -> None:
        event = str(message.get("event", ""))
        if event == "hello":
            self.send_result("ready", "PC cup game server is ready.")
        elif event == "start_round":
            self.start_round()
        elif event == "pc_audio_turn":
            if self.uses_pc_keyboard_input():
                logger.warning("Ignoring pc_audio_turn because keyboard control mode is active")
            else:
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

    def handle_pepper_speech_done(self, message: Dict[str, object]) -> None:
        token = str(message.get("speech_token", "")).strip()
        logger.info(
            "Pepper speech completed: event=%s token=%s text=%s",
            message.get("speech_event", ""),
            token or "[missing]",
            message.get("text", ""),
        )
        with self.pepper_speech_condition:
            if token:
                self.completed_pepper_speech_tokens.add(token)
            self.pepper_speech_condition.notify_all()

    def handle_audio_utterance(self, pcm: bytes) -> None:
        if self.pc_audio_fallback_active:
            logger.info("Ignoring Pepper audio because PC audio fallback is active")
            return
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

    def handle_pc_audio_turn(self, capture_seconds: Optional[float] = None) -> None:
        if self.flags["audio_input_mode"] != "pc" and not self.pc_audio_fallback_active:
            self.pc_audio_fallback_active = True
            logger.warning(
                "PC audio requested while audio_input_mode=%s; enabling PC audio fallback",
                self.flags["audio_input_mode"],
            )
        logger.info("Starting PC microphone capture turn")
        listening_message = "Beginning to listen now. Please speak clearly into the PC microphone."
        if self.flags["speech_output_mode"] == "pepper":
            # In Pepper-output/PC-input mode, do not make Pepper say an extra
            # listening prompt here. The previous prompt has already told the
            # user what to do, and another TTS round often causes the PC mic to
            # miss a quick answer.
            self.send_result("pc_audio_started", "")
        elif self.pc_audio_barge_in_enabled:
            # Start recording immediately so a user can answer while the prompt is still playing.
            self.send_result("pc_audio_started", listening_message)
        else:
            self.speech.wait_for_idle()
            self.say(listening_message, event="pc_audio_started", wait=True)
        logger.info("PC microphone is listening now")
        text = self.audio.capture_and_transcribe_pc_mic(
            capture_seconds or self.pc_mic_capture_seconds,
            on_speech_start=self.stop_pc_prompt_for_barge_in if self.pc_audio_barge_in_enabled else None,
        )
        if text:
            logger.info("PC microphone heard: %s", text)
            self.speech.stop_all()
            self.handle_spoken_input(text)
        else:
            logger.info("PC microphone capture produced no recognized speech")
            if self.awaiting_replay_response:
                self.prompt_for_replay(listen=True, prefix="I did not catch that. ")
            else:
                self.prompt_for_control(listen=True, prefix="I did not catch that. ")

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

        normalized_bottom = self.normalize_color_sequence(bottom_row)
        normalized_top = self.normalize_color_sequence(top_row)

        if self.awaiting_vision_target_shuffle:
            shuffle_outcome = self.validate_vision_target_shuffle(normalized_bottom, result)
            if shuffle_outcome:
                self.say(str(shuffle_outcome["reply"]), event="vision_checked", data=shuffle_outcome)
                self.prompt_for_control(listen=True)
                return

        if normalized_bottom:
            self.game.set_target_sequence(normalized_bottom)
        if normalized_top:
            outcome = self.game.evaluate_sequence(normalized_top)
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
            self.prompt_after_attempt(outcome)
        else:
            self.prompt_for_replay(listen=True)

    def handle_guess(self, text: str) -> None:
        logger.info("Checking user guess text: %s", text)
        outcome = self.game.check_guess(text)
        logger.info("Guess outcome: %s", outcome)
        self.say(outcome["reply"], event="guess_checked", data=outcome)
        if not bool(outcome.get("finished", False)):
            self.prompt_after_attempt(outcome)
        else:
            self.prompt_for_replay(listen=True)

    def handle_spoken_input(self, text: str) -> None:
        if self.awaiting_replay_response:
            self.handle_replay_response(text)
            return

        command = self.game.classify_command(text)
        logger.info("Spoken input classified as %s: %s", command, text)
        if command == "check":
            self.record_hint_offer_response(accepted=False)
            self.say("Okay, checking now.", wait=False)
            if self.flags["vision_input_mode"] == "pc":
                self.handle_pc_vision_check()
            elif self.flags["vision_input_mode"] == "pepper":
                self.request_pepper_vision_check()
                self.send_result("vision_requested", "Checking the Pepper camera frame.")
            else:
                self.send_result("config_error", "Invalid vision_input_mode.")
        elif command == "hint":
            self.record_hint_offer_response(accepted=True)
            outcome = self.game.hint()
            logger.info("Hint outcome: %s", outcome)
            self.say(str(outcome["reply"]), event="hint", data=outcome)
            self.prompt_for_control(listen=True, pause_before_listen=True)
        else:
            if self.uses_vision_sequence_input():
                logger.info("Ignoring non-control spoken input while vision mode is active: %s", text)
                self.prompt_for_control(
                    listen=True,
                    prefix="I did not hear ready, check, or help. ",
                )
                return
            self.handle_guess(text)

    def start_round(
        self,
        shuffle_target: bool = False,
        require_vision_target_shuffle: bool = False,
        announce_rules: bool = True,
    ) -> None:
        self.awaiting_replay_response = False
        self.pomdp = PepperPOMDP()
        self.hint_offer_pending = False
        self.last_hint_offer_attempt = 0
        self.last_control_prompt_at = time.monotonic()
        if require_vision_target_shuffle:
            self.previous_vision_target = list(self.game.target_sequence)
            self.awaiting_vision_target_shuffle = True
            logger.info("Vision replay requires bottom-row shuffle from previous target=%s", self.previous_vision_target)
        else:
            self.previous_vision_target = None
            self.awaiting_vision_target_shuffle = False
        if shuffle_target:
            self.game.shuffle_target_sequence()
        round_state = self.game.start_round()
        logger.info(
            "Started sequence game: target_length=%s max_attempts=%s",
            round_state["target_length"],
            round_state["max_attempts"],
        )
        message = round_state["instruction"] if announce_rules else "New game started."
        self.say(message, event="round_started", data=round_state)
        self.prompt_for_control(listen=True)

    def prompt_after_attempt(self, outcome: Dict[str, object]) -> None:
        if self.should_offer_pomdp_hint(outcome):
            self.hint_offer_pending = True
            self.last_hint_offer_attempt = int(outcome.get("attempt", self.game.attempt_number))
            self.say(
                "Would you like a hint? Press H for help, or C to keep trying.",
                event="hint_offer",
                data={
                    "pomdp_belief": list(self.pomdp.belief),
                    "attempt": int(outcome.get("attempt", self.game.attempt_number)),
                },
            )
            self.prompt_for_control(listen=True, pause_before_listen=True)
            return
        self.prompt_for_control(listen=True, pause_before_listen=True)

    def should_offer_pomdp_hint(self, outcome: Dict[str, object]) -> bool:
        if not self.pomdp_hint_policy_enabled:
            return False
        if bool(outcome.get("finished", False)):
            return False
        attempt = int(outcome.get("attempt", self.game.attempt_number))
        if attempt < self.pomdp_min_attempts_before_offer:
            return False
        if self.hint_offer_pending:
            return False
        if attempt - self.last_hint_offer_attempt < self.pomdp_offer_cooldown_attempts:
            return False

        move_seconds = max(time.monotonic() - self.last_control_prompt_at, 0.0)
        observation = 0 if move_seconds < self.pomdp_fast_move_seconds else 1
        self.pomdp.update_belief("act_wait", observation)
        action, values = self.pomdp.select_action()
        logger.info(
            "POMDP hint policy: move_seconds=%.1f observation=%s belief=%s values=%s action=%s",
            move_seconds,
            "fast" if observation == 0 else "slow",
            self.pomdp.belief,
            values,
            action,
        )
        return action == "act_offerHint"

    def record_hint_offer_response(self, accepted: bool) -> None:
        if not self.hint_offer_pending:
            return
        observation = 0 if accepted else 1
        self.pomdp.update_belief("act_offerHint", observation)
        logger.info(
            "POMDP hint offer %s: belief=%s",
            "accepted" if accepted else "rejected",
            self.pomdp.belief,
        )
        self.hint_offer_pending = False

    def prompt_for_replay(self, listen: bool = False, prefix: str = "") -> None:
        self.awaiting_replay_response = True
        self.say_listening_prompt(
            "%sDo you want to play again? Press Y for yes, or N for no." % prefix,
            event="play_again_prompt",
            listen=listen,
        )
        if listen and self.uses_pc_keyboard_input():
            self.handle_pc_keyboard_turn()
        elif listen and self.uses_pc_audio_input():
            self.handle_pc_audio_turn()

    def handle_replay_response(self, text: str) -> None:
        lowered = text.lower()
        if re.search(r"\b(yes|yeah|yep|sure|again|play again|restart)\b", lowered):
            logger.info("Replay accepted by user: %s", text)
            self.say(self.replay_start_message(), event="play_again", wait=False)
            self.start_round(
                shuffle_target=self.should_shuffle_target_on_replay(),
                require_vision_target_shuffle=self.should_require_target_shuffle_on_replay(),
                announce_rules=False,
            )
            return
        if re.search(r"\b(no|nope|stop|not now|quit)\b", lowered):
            logger.info("Replay declined by user: %s", text)
            self.awaiting_replay_response = False
            self.say("Okay. The game is finished.", event="play_again")
            return
        logger.info("Replay response unclear: %s", text)
        self.prompt_for_replay(
            listen=self.uses_pc_audio_input() or self.uses_pc_keyboard_input(),
            prefix="Please press Y to play again, or N to finish. ",
        )

    def prompt_for_control(
        self,
        listen: bool = False,
        prefix: str = "",
        pause_before_listen: bool = False,
    ) -> None:
        if (self.uses_pc_audio_input() or self.uses_pc_keyboard_input()) and not self.game.finished:
            logger.info("Ready for user control: c=check h=help")
            prompt = self.control_prompt_text(pause_before_listen=pause_before_listen)
            self.last_control_prompt_at = time.monotonic()
            self.say_listening_prompt("%s%s" % (prefix, prompt), listen=listen)
            if listen:
                if self.uses_pc_keyboard_input():
                    self.handle_pc_keyboard_turn()
                    return
                capture_seconds = self.pc_mic_capture_seconds
                if pause_before_listen and self.rearrange_pause_seconds > 0:
                    capture_seconds += self.rearrange_pause_seconds
                    logger.info(
                        "Listening for %.1f seconds so the player can rearrange cups and answer when ready",
                        capture_seconds,
                    )
                self.handle_pc_audio_turn(capture_seconds=capture_seconds)

    def control_prompt_text(self, pause_before_listen: bool = False) -> str:
        if self.uses_vision_sequence_input():
            if pause_before_listen:
                return "Take a moment to rearrange the cups. Press C when you want me to check. Press H if you need help."
            return "Press C when you want me to check the top row. Press H if you need help."
        return "Press C when you want me to check the top row. Press H if you need help."

    def uses_voice_sequence_input(self) -> bool:
        return str(self.flags.get("player_input_mode", "")).lower() == "speech"

    def uses_pc_audio_input(self) -> bool:
        return str(self.flags.get("audio_input_mode", "")).lower() == "pc" or self.pc_audio_fallback_active

    def uses_pc_keyboard_input(self) -> bool:
        return str(self.flags.get("pc_control_input_mode", "speech")).lower() in ("keyboard", "hybrid")

    def uses_pc_hybrid_input(self) -> bool:
        return str(self.flags.get("pc_control_input_mode", "speech")).lower() == "hybrid"

    def handle_pc_keyboard_turn(self) -> None:
        if self.uses_pc_hybrid_input():
            self.handle_pc_hybrid_turn()
            return

        with self.keyboard_lock:
            if self.awaiting_replay_response:
                logger.warning("Waiting for keyboard replay input: y=yes n=no")
                key = self.read_pc_key("Play again? Press Y for yes, or N for no: ")
                if key == "y":
                    self.handle_replay_response("yes")
                elif key == "n":
                    self.handle_replay_response("no")
                else:
                    logger.warning("Ignored keyboard input for replay: %s", key)
                    self.prompt_for_replay(listen=True, prefix="I did not understand that key. ")
                return

            logger.warning("Waiting for keyboard input: c=check h=help")
            key = self.read_pc_key("Press C to check, or H for help: ")
            if key == "c":
                self.handle_spoken_input("check")
            elif key == "h":
                self.handle_spoken_input("help")
            else:
                logger.warning("Ignored keyboard input for control: %s", key)
                self.prompt_for_control(listen=True, prefix="I did not understand that key. ")

    def handle_pc_hybrid_turn(self) -> None:
        with self.keyboard_lock:
            if self.awaiting_replay_response:
                logger.info("Listening for replay response; keyboard override: y=yes n=no")
                text = self.audio.listen_with_streaming_whisper(
                    self.pc_mic_capture_seconds,
                    on_speech_start=self.stop_pc_prompt_for_barge_in if self.pc_audio_barge_in_enabled else None,
                    accept_transcript=self.is_replay_transcript,
                    poll_key=self.poll_replay_key,
                )
                if text:
                    logger.info("Hybrid replay input accepted: %s", text)
                    self.handle_replay_response(text)
                else:
                    self.prompt_for_replay(listen=True, prefix="I did not catch that. ")
                return

            logger.info("Listening for control command; keyboard override: c=check h=help")
            text = self.audio.listen_with_streaming_whisper(
                self.pc_mic_capture_seconds,
                on_speech_start=self.stop_pc_prompt_for_barge_in if self.pc_audio_barge_in_enabled else None,
                accept_transcript=self.is_control_transcript,
                poll_key=self.poll_control_key,
            )
            if text:
                logger.info("Hybrid control input accepted: %s", text)
                self.handle_spoken_input(text)
            else:
                self.prompt_for_control(listen=True, prefix="I did not hear check or help. ")

    def is_control_transcript(self, text: str) -> bool:
        return self.game.classify_command(text) in ("check", "hint")

    def is_replay_transcript(self, text: str) -> bool:
        lowered = text.lower()
        return bool(re.search(r"\b(yes|yeah|yep|sure|again|play again|restart|no|nope|stop|not now|quit)\b", lowered))

    def poll_control_key(self) -> Optional[str]:
        key = self.poll_pc_key()
        if key == "c":
            return "check"
        if key == "h":
            return "help"
        if key:
            logger.warning("Ignored keyboard input while listening for control: %s", key)
        return None

    def poll_replay_key(self) -> Optional[str]:
        key = self.poll_pc_key()
        if key == "y":
            return "yes"
        if key == "n":
            return "no"
        if key:
            logger.warning("Ignored keyboard input while listening for replay: %s", key)
        return None

    def read_pc_key(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        if msvcrt is not None:
            key = msvcrt.getwch()
            print(key)
            return key.lower()
        return input().strip().lower()[:1]

    def poll_pc_key(self) -> Optional[str]:
        if msvcrt is None or not msvcrt.kbhit():
            return None
        key = msvcrt.getwch()
        print(key)
        return key.lower()

    def say_listening_prompt(self, message: str, event: str = "say", listen: bool = False) -> None:
        # These prompts tell the user what can be said next, so queue them after
        # feedback instead of silently skipping the instruction.
        if self.pc_audio_barge_in_enabled and self.speech.is_busy():
            logger.info("Waiting for active PC speech before listening prompt: %s", message)
            self.speech.wait_for_idle()
        speech_token = self.say(message, event=event, wait=(listen and not self.pc_audio_barge_in_enabled))
        if listen:
            self.wait_for_pepper_speech_before_pc_input(event, speech_token)

    def stop_pc_prompt_for_barge_in(self) -> bool:
        if self.speech.is_busy():
            logger.info("User speech detected during PC prompt; stopping prompt for barge-in")
            self.speech.stop_all()
            return True
        return False

    def should_wait_for_pepper_speech_before_pc_input(self) -> bool:
        return (
            self.flags["speech_output_mode"] == "pepper"
            and (self.uses_pc_audio_input() or self.uses_pc_keyboard_input())
        )

    def arm_pepper_speech_wait(self, event: str, text: str) -> Optional[str]:
        if self.flags["speech_output_mode"] != "pepper" or not text:
            return None
        with self.pepper_speech_condition:
            self.next_pepper_speech_token += 1
            token = "%s-%s-%s" % (int(time.time() * 1000), self.next_pepper_speech_token, event)
        logger.info("Arming Pepper speech completion wait: event=%s token=%s", event, token)
        return token

    def wait_for_pepper_speech_before_pc_input(self, event: str, speech_token: Optional[str]) -> None:
        if not self.should_wait_for_pepper_speech_before_pc_input():
            return
        if not speech_token:
            logger.info("No Pepper speech token for event=%s; starting PC input without speech wait", event)
            return

        logger.info("Waiting for Pepper speech to finish before PC input: event=%s token=%s", event, speech_token)
        deadline = time.monotonic() + self.pepper_speech_timeout_seconds
        with self.pepper_speech_condition:
            while speech_token not in self.completed_pepper_speech_tokens:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.pepper_speech_condition.wait(remaining)

            if speech_token in self.completed_pepper_speech_tokens:
                self.completed_pepper_speech_tokens.discard(speech_token)
                logger.info("Pepper speech finished; PC input may start: token=%s", speech_token)
                return

        logger.warning(
            "Timed out waiting %.1f seconds for Pepper speech completion; starting PC input anyway: token=%s",
            self.pepper_speech_timeout_seconds,
            speech_token,
        )

    def uses_vision_sequence_input(self) -> bool:
        return str(self.flags.get("player_input_mode", "")).lower() == "vision"

    def should_shuffle_target_on_replay(self) -> bool:
        return self.uses_voice_sequence_input()

    def should_require_target_shuffle_on_replay(self) -> bool:
        return self.uses_vision_sequence_input()

    def replay_start_message(self) -> str:
        if self.uses_voice_sequence_input():
            return "Great. I picked a new hidden sequence. Starting a new game."
        if self.uses_vision_sequence_input():
            return "Great. Please shuffle the bottom row, then say ready or check."
        return "Great. Starting a new game."

    def normalize_color_sequence(self, value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(color).strip().lower() for color in value if str(color).strip()]

    def validate_vision_target_shuffle(
        self,
        bottom_row: List[str],
        vision_result: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        target_length = len(self.previous_vision_target or self.game.target_sequence)
        if len(bottom_row) != target_length:
            return {
                "status": "needs_target_shuffle",
                "reply": "I need to read all %s cups in the bottom row before we start. Please shuffle the bottom row, then say ready or check." % target_length,
                "bottom_row_detected": bottom_row,
                "vision": vision_result,
            }
        if self.previous_vision_target and bottom_row == self.previous_vision_target:
            return {
                "status": "needs_target_shuffle",
                "reply": "The bottom row still looks the same. Please shuffle the bottom row before we start again.",
                "bottom_row_detected": bottom_row,
                "previous_target": self.previous_vision_target,
                "vision": vision_result,
            }

        self.awaiting_vision_target_shuffle = False
        self.previous_vision_target = None
        logger.info("Accepted shuffled vision target: %s", bottom_row)
        return None

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

    def say(
        self,
        message: str,
        event: str = "say",
        data: Optional[Dict[str, object]] = None,
        wait: bool = False,
    ) -> Optional[str]:
        logger.info("Preparing Pepper speech event=%s message=%s", event, message)
        polished = message if self.should_use_literal_speech(event) else self.llm.polish_for_pepper(message)
        if polished != message:
            logger.info("LM Studio polished message: %s", polished)
        speech_token = self.arm_pepper_speech_wait(event, polished)
        payload_data = dict(data or {})
        if speech_token:
            payload_data["speech_token"] = speech_token
        if self.flags["speech_output_mode"] == "pc":
            logger.info("[PC SPEECH] %s", polished)
            self.speech.say(polished, wait=wait)
        self.send_result(event, polished, payload_data)
        return speech_token

    def should_use_literal_speech(self, event: str) -> bool:
        return event in {
            "pc_audio_started",
            "ready",
            "round_started",
            "say",
            "guess_checked",
            "vision_checked",
            "hint",
            "hint_offer",
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
