# -*- coding: utf-8 -*-
import json
import socket
import struct
import threading
import time


PC_IP = "127.0.0.1"
COMMAND_PORT = 50010
AUDIO_PORT = 50011
VISION_PORT = 50012
RESULT_PORT = 50013

# Manual user flags. Change these before running the Choregraphe behavior.
AUDIO_INPUT_MODE = "pc"   # "pepper" or "pc"
VISION_INPUT_MODE = "pc"  # "pepper" or "pc"
SPEECH_OUTPUT_MODE = "pc" # "pepper" or "pc"
PLAYER_INPUT_MODE = "speech" # "vision" or "speech"


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)
        self.command_socket = None
        self.audio_socket = None
        self.result_socket = None
        self.audio = None
        self.video = None
        self.basic_awareness = None
        self.tts = None
        self.tts_stop = None
        self.video_subscriber = None
        self.result_thread = None
        self.bIsRunning = False
        self.speech_ids = []
        self.awareness_was_enabled = None
        self.vision_motion_paused = False

    def onLoad(self):
        self.bIsRunning = False
        self.speech_ids = []
        self.logger.info("Cup game entry box loaded.")
        self.logger.info("PC_IP=" + str(PC_IP))
        self.logger.info("AUDIO_INPUT_MODE=" + str(AUDIO_INPUT_MODE))
        self.logger.info("VISION_INPUT_MODE=" + str(VISION_INPUT_MODE))
        self.logger.info("SPEECH_OUTPUT_MODE=" + str(SPEECH_OUTPUT_MODE))
        self.logger.info("PLAYER_INPUT_MODE=" + str(PLAYER_INPUT_MODE))
        try:
            self.tts = ALProxy("ALTextToSpeech")
            self.tts_stop = ALProxy("ALTextToSpeech", True)
            self.logger.info("ALTextToSpeech proxy ready.")
        except Exception as e:
            self.logger.warning("TTS unavailable: " + str(e))
        try:
            self.audio = ALProxy("ALAudioDevice")
            self.logger.info("ALAudioDevice proxy ready.")
        except Exception as e:
            self.logger.warning("ALAudioDevice unavailable: " + str(e))
            self.audio = None
        try:
            self.video = ALProxy("ALVideoDevice")
            self.logger.info("ALVideoDevice proxy ready.")
        except Exception as e:
            self.logger.warning("ALVideoDevice unavailable: " + str(e))
            self.video = None
        try:
            self.basic_awareness = ALProxy("ALBasicAwareness")
            self.logger.info("ALBasicAwareness proxy ready.")
        except Exception as e:
            self.logger.warning("ALBasicAwareness unavailable: " + str(e))
            self.basic_awareness = None

    def onUnload(self):
        self.logger.info("Cup game entry box unloading.")
        self.bIsRunning = False
        self.restore_head_motion_after_vision()
        self.unsubscribe_audio()
        self.unsubscribe_video()
        self.stop_speech()
        self.close_socket(self.command_socket)
        self.command_socket = None
        self.close_socket(self.audio_socket)
        self.audio_socket = None
        self.close_socket(self.result_socket)
        self.result_socket = None

    def onInput_onStart(self):
        self.bIsRunning = True
        self.logger.info("Cup game entry starting.")
        try:
            self.connect_command_socket()
            self.connect_result_socket()
            self.logger.info("Sending hello and start_round commands.")
            self.send_command({"event": "hello"})
            self.send_command({"event": "start_round"})
            self.start_result_listener()

            if PLAYER_INPUT_MODE == "vision":
                if VISION_INPUT_MODE == "pc":
                    self.logger.info("Vision delegated to PC by manual flag.")
                    self.pause_head_motion_for_vision()
                    self.send_command({"event": "pc_vision_check"})
                elif VISION_INPUT_MODE == "pepper":
                    self.logger.info("Vision handled by Pepper camera by manual flag.")
                    self.capture_and_send_pepper_frame()
                else:
                    self.logger.error("Invalid VISION_INPUT_MODE: " + str(VISION_INPUT_MODE))
            elif PLAYER_INPUT_MODE == "speech":
                if AUDIO_INPUT_MODE == "pc":
                    self.logger.info("Audio delegated to PC by manual flag.")
                    self.send_command({"event": "pc_audio_turn"})
                elif AUDIO_INPUT_MODE == "pepper":
                    self.logger.info("Audio handled by Pepper microphone by manual flag.")
                    self.connect_audio_socket()
                    self.subscribe_audio()
                else:
                    self.logger.error("Invalid AUDIO_INPUT_MODE: " + str(AUDIO_INPUT_MODE))
            else:
                self.logger.error("Invalid PLAYER_INPUT_MODE: " + str(PLAYER_INPUT_MODE))
        except Exception as e:
            self.logger.error("Cup game entry failed: " + str(e))
            self.onInput_onStop()

    def connect_command_socket(self):
        self.logger.info("Connecting command socket to " + str(PC_IP) + ":" + str(COMMAND_PORT))
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.command_socket.connect((PC_IP, COMMAND_PORT))
        self.logger.info("Command socket connected.")

    def connect_audio_socket(self):
        self.logger.info("Connecting audio socket to " + str(PC_IP) + ":" + str(AUDIO_PORT))
        self.audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_socket.connect((PC_IP, AUDIO_PORT))
        self.logger.info("Audio socket connected.")

    def connect_result_socket(self):
        self.logger.info("Connecting result socket to " + str(PC_IP) + ":" + str(RESULT_PORT))
        self.result_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.result_socket.connect((PC_IP, RESULT_PORT))
        self.logger.info("Result socket connected.")

    def start_result_listener(self):
        self.logger.info("Starting result listener thread.")
        self.result_thread = threading.Thread(target=self.listen_for_results)
        self.result_thread.daemon = True
        self.result_thread.start()

    def send_command(self, payload):
        self.logger.info("Sending command payload: " + str(payload))
        if not self.command_socket:
            raise RuntimeError("Command socket is not connected.")
        try:
            self.command_socket.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except Exception as e:
            self.logger.error("Command socket send failed: " + str(e))
            self.close_socket(self.command_socket)
            self.command_socket = None
            raise

    def send_blob(self, sock, data):
        sock.sendall(struct.pack("!I", len(data)) + data)

    def send_image(self, width, height, data):
        self.logger.info("Sending image frame width=%s height=%s bytes=%s" % (width, height, len(data)))
        header = struct.pack("!III", width, height, len(data))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((PC_IP, VISION_PORT))
            sock.sendall(header + data)
        finally:
            sock.close()

    def recv_json_line(self, sock):
        chunks = []
        while self.bIsRunning:
            char = sock.recv(1)
            if not char:
                return None
            if char == "\n":
                break
            chunks.append(char)
        return json.loads("".join(chunks))

    def listen_for_results(self):
        self.logger.info("Result listener active.")
        while self.bIsRunning:
            try:
                message = self.recv_json_line(self.result_socket)
                if message is None:
                    return
                text = str(message.get("text", ""))
                event = str(message.get("event", ""))
                self.logger.info("Received result event=%s text=%s" % (event, text))
                if event in ("vision_checked", "mode_ignored", "config_error"):
                    self.restore_head_motion_after_vision()
                if text:
                    self.handle_speech(text)
            except Exception as e:
                self.logger.error("Result listener stopped: " + str(e))
                self.restore_head_motion_after_vision()
                return

    def handle_speech(self, text):
        self.logger.info("PC result: " + text)
        if SPEECH_OUTPUT_MODE != "pepper":
            self.logger.info("Speech output mode is PC, so Pepper will not speak this result.")
            return
        if not self.tts:
            return
        if AUDIO_INPUT_MODE == "pepper":
            self.unsubscribe_audio()
        speech_id = self.tts.post.say(text)
        self.logger.info("Started Pepper TTS job id=" + str(speech_id))
        self.speech_ids.append(speech_id)
        self.tts.wait(speech_id, 0)
        try:
            self.speech_ids.remove(speech_id)
        except Exception:
            pass
        if AUDIO_INPUT_MODE == "pepper" and self.bIsRunning:
            self.subscribe_audio()

    def subscribe_audio(self):
        if not self.audio:
            self.logger.error("AUDIO_INPUT_MODE is pepper, but ALAudioDevice is unavailable.")
            return
        self.audio.setClientPreferences(self.getName(), 16000, 3, 0)
        self.audio.subscribe(self.getName())
        self.logger.info("Pepper microphone streaming started.")

    def unsubscribe_audio(self):
        if self.audio:
            try:
                self.audio.unsubscribe(self.getName())
            except Exception:
                pass

    def capture_and_send_pepper_frame(self):
        if not self.video:
            self.logger.error("VISION_INPUT_MODE is pepper, but ALVideoDevice is unavailable.")
            return
        camera_index = 0
        resolution = 1
        color_space = 11
        fps = 10
        width = 320
        height = 240
        self.pause_head_motion_for_vision()
        try:
            self.video.setActiveCamera(camera_index)
            self.video_subscriber = self.video.subscribeCamera(
                "cup_game_camera", camera_index, resolution, color_space, fps
            )
            image = self.video.getImageRemote(self.video_subscriber)
            if image is None:
                self.logger.error("No Pepper camera image received.")
                return
            self.send_image(width, height, image[6])
        finally:
            self.restore_head_motion_after_vision()

    def pause_head_motion_for_vision(self):
        if not self.basic_awareness or self.vision_motion_paused:
            return
        try:
            self.awareness_was_enabled = self.basic_awareness.isEnabled()
            if self.awareness_was_enabled:
                self.basic_awareness.setEnabled(False)
                self.logger.info("Basic awareness paused for vision capture.")
            self.vision_motion_paused = True
        except Exception as e:
            self.logger.warning("Could not pause basic awareness for vision: " + str(e))

    def restore_head_motion_after_vision(self):
        if not self.basic_awareness or not self.vision_motion_paused:
            return
        try:
            if self.awareness_was_enabled:
                self.basic_awareness.setEnabled(True)
                self.logger.info("Basic awareness restored after vision capture.")
        except Exception as e:
            self.logger.warning("Could not restore basic awareness after vision: " + str(e))
        self.awareness_was_enabled = None
        self.vision_motion_paused = False

    def unsubscribe_video(self):
        if self.video and self.video_subscriber:
            try:
                self.video.unsubscribe(self.video_subscriber)
                self.logger.info("Pepper camera unsubscribed.")
            except Exception:
                pass
            self.video_subscriber = None

    def processRemote(self, nbOfChannels, nbrOfSamplesByChannel, timestamp, buffer):
        if not self.bIsRunning or AUDIO_INPUT_MODE != "pepper":
            return
        try:
            self.send_blob(self.audio_socket, buffer)
        except Exception as e:
            self.logger.error("Audio stream failed: " + str(e))

    def stop_speech(self):
        if not self.tts_stop:
            return
        for speech_id in self.speech_ids:
            try:
                self.tts_stop.stop(speech_id)
            except Exception:
                pass

    def close_socket(self, sock):
        try:
            if sock:
                sock.close()
        except Exception:
            pass

    def onInput_onStop(self):
        self.logger.info("Cup game entry stopping.")
        if self.command_socket:
            try:
                self.send_command({"event": "stop"})
            except Exception:
                pass
        self.onUnload()
        self.onStopped()
