import json
import socket
import struct
import threading
import time


PC_IP = "169.254.61.236"
COMMAND_PORT = 50010
AUDIO_PORT = 50011
VISION_PORT = 50012
RESULT_PORT = 50013

# Manual user flags. Change these before running the Choregraphe behavior.
AUDIO_INPUT_MODE = "pepper"   # "pepper" or "pc"
VISION_INPUT_MODE = "pepper"  # "pepper" or "pc"
SPEECH_OUTPUT_MODE = "pepper" # "pepper" or "pc"
PLAYER_INPUT_MODE = "speech"  # "vision" or "speech"

PEPPER_VISION_STREAM_ENABLED = True
PEPPER_CAMERA_RESOLUTION = 2  # 1=320x240, 2=640x480
PEPPER_CAMERA_FPS = 5
PEPPER_AUDIO_STREAM_ENABLED = False
PEPPER_HEAD_YAW = 0.0
PEPPER_HEAD_PITCH = 0.25


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)

        self.command_socket = None
        self.audio_socket = None
        self.result_socket = None

        self.audio = None
        self.audio_subscribed = False
        self.video = None
        self.basic_awareness = None
        self.motion = None
        self.tts = None
        self.tts_stop = None

        self.video_subscriber = None
        self.vision_stream_thread = None
        self.vision_streaming = False
        self.result_thread = None

        self.bIsRunning = False
        self.speech_ids = []

        self.awareness_was_enabled = None
        self.vision_motion_paused = False
        self.head_was_stiff = None
        self.head_angles_before_vision = None
        self.last_head_lock_refresh = 0.0

    def onLoad(self):
        self.bIsRunning = False
        self.speech_ids = []

        self.logger.info("Cup game entry box loaded.")
        self.logger.info("PC_IP=" + str(PC_IP))
        self.logger.info("AUDIO_INPUT_MODE=" + str(AUDIO_INPUT_MODE))
        self.logger.info("VISION_INPUT_MODE=" + str(VISION_INPUT_MODE))
        self.logger.info("SPEECH_OUTPUT_MODE=" + str(SPEECH_OUTPUT_MODE))
        self.logger.info("PLAYER_INPUT_MODE=" + str(PLAYER_INPUT_MODE))
        self.logger.info("PEPPER_CAMERA_RESOLUTION=" + str(PEPPER_CAMERA_RESOLUTION))
        self.logger.info("PEPPER_CAMERA_FPS=" + str(PEPPER_CAMERA_FPS))
        self.logger.info("PEPPER_AUDIO_STREAM_ENABLED=" + str(PEPPER_AUDIO_STREAM_ENABLED))
        self.logger.info("PEPPER_HEAD_YAW=" + str(PEPPER_HEAD_YAW))
        self.logger.info("PEPPER_HEAD_PITCH=" + str(PEPPER_HEAD_PITCH))

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

        try:
            self.motion = ALProxy("ALMotion")
            self.logger.info("ALMotion proxy ready.")
        except Exception as e:
            self.logger.warning("ALMotion unavailable: " + str(e))
            self.motion = None

    def onUnload(self):
        self.logger.info("Cup game entry box unloading.")

        self.bIsRunning = False
        self.stop_pepper_vision_stream()
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

            if VISION_INPUT_MODE == "pepper" and PEPPER_VISION_STREAM_ENABLED:
                self.start_pepper_vision_stream()

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
                    if not PEPPER_AUDIO_STREAM_ENABLED:
                        self.logger.warning(
                            "Pepper microphone streaming is disabled; game stays open for vision preview and PC results."
                        )
                    elif self.subscribe_audio():
                        self.connect_audio_socket()
                    else:
                        self.logger.warning(
                            "Pepper microphone input is unavailable; game stays open for vision preview and PC results."
                        )

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
        self.logger.info(
            "Sending image frame width=%s height=%s bytes=%s"
            % (width, height, len(data))
        )

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

        audio_was_subscribed = self.audio_subscribed

        if AUDIO_INPUT_MODE == "pepper" and audio_was_subscribed:
            self.unsubscribe_audio()

        speech_id = self.tts.post.say(text)
        self.logger.info("Started Pepper TTS job id=" + str(speech_id))
        self.speech_ids.append(speech_id)

        self.tts.wait(speech_id, 0)

        try:
            self.speech_ids.remove(speech_id)
        except Exception:
            pass

        if AUDIO_INPUT_MODE == "pepper" and self.bIsRunning and audio_was_subscribed:
            self.subscribe_audio()

    def subscribe_audio(self):
        if not self.audio:
            self.logger.error("AUDIO_INPUT_MODE is pepper, but ALAudioDevice is unavailable.")
            return False

        try:
            self.audio.setClientPreferences(self.getName(), 16000, 3, 0)
            self.audio.subscribe(self.getName())
            self.audio_subscribed = True
            self.logger.info("Pepper microphone streaming started.")
            return True
        except Exception as e:
            self.audio_subscribed = False
            self.logger.error("Pepper microphone streaming could not start: " + str(e))
            self.close_socket(self.audio_socket)
            self.audio_socket = None
            return False

    def unsubscribe_audio(self):
        if self.audio and self.audio_subscribed:
            try:
                self.audio.unsubscribe(self.getName())
                self.logger.info("Pepper microphone unsubscribed.")
            except Exception as e:
                self.logger.warning("Pepper microphone unsubscribe failed: " + str(e))
            self.audio_subscribed = False

    def capture_and_send_pepper_frame(self):
        if not self.video:
            self.logger.error("VISION_INPUT_MODE is pepper, but ALVideoDevice is unavailable.")
            return

        camera_index = 0
        resolution = PEPPER_CAMERA_RESOLUTION
        color_space = 11
        fps = PEPPER_CAMERA_FPS

        width, height = self.camera_dimensions(resolution)

        self.pause_head_motion_for_vision()

        try:
            self.video.setActiveCamera(camera_index)

            self.video_subscriber = self.video.subscribeCamera(
                "cup_game_camera",
                camera_index,
                resolution,
                color_space,
                fps
            )

            image = self.video.getImageRemote(self.video_subscriber)

            if image is None:
                self.logger.error("No Pepper camera image received.")
                return

            frame_width = int(image[0]) if image[0] else width
            frame_height = int(image[1]) if image[1] else height

            self.send_image(frame_width, frame_height, image[6])

        finally:
            self.unsubscribe_video()
            self.restore_head_motion_after_vision()

    def start_pepper_vision_stream(self):
        if not self.video:
            self.logger.error("Cannot start Pepper vision stream because ALVideoDevice is unavailable.")
            return

        if self.vision_streaming:
            return

        self.pause_head_motion_for_vision()

        self.vision_streaming = True
        self.vision_stream_thread = threading.Thread(target=self.pepper_vision_stream_loop)
        self.vision_stream_thread.daemon = True
        self.vision_stream_thread.start()

        self.logger.info("Pepper vision stream thread started.")

    def stop_pepper_vision_stream(self):
        self.vision_streaming = False
        if self.vision_stream_thread:
            try:
                self.vision_stream_thread.join(1.0)
            except Exception:
                pass
            self.vision_stream_thread = None
        self.restore_head_motion_after_vision(True)

    def pepper_vision_stream_loop(self):
        camera_index = 0
        resolution = PEPPER_CAMERA_RESOLUTION
        color_space = 11
        fps = PEPPER_CAMERA_FPS

        width, height = self.camera_dimensions(resolution)
        period = 1.0 / float(max(fps, 1))

        subscriber = None

        try:
            self.video.setActiveCamera(camera_index)

            subscriber = self.video.subscribeCamera(
                "cup_game_camera_stream",
                camera_index,
                resolution,
                color_space,
                fps
            )

            self.video_subscriber = subscriber

            self.logger.info(
                "Pepper camera stream active resolution=%s width=%s height=%s fps=%s"
                % (resolution, width, height, fps)
            )

            while self.bIsRunning and self.vision_streaming:
                self.refresh_head_lock_for_vision()
                image = self.video.getImageRemote(subscriber)

                if image is None:
                    self.logger.warning("No Pepper camera stream image received.")
                    time.sleep(period)
                    continue

                try:
                    frame_width = int(image[0]) if image[0] else width
                    frame_height = int(image[1]) if image[1] else height
                except Exception:
                    frame_width = width
                    frame_height = height

                self.send_image(frame_width, frame_height, image[6])
                time.sleep(period)

        except Exception as e:
            self.logger.error("Pepper vision stream failed: " + str(e))

        finally:
            if self.video and subscriber:
                try:
                    self.video.unsubscribe(subscriber)
                    self.logger.info("Pepper camera stream unsubscribed.")
                except Exception:
                    pass

            if self.video_subscriber == subscriber:
                self.video_subscriber = None

            self.vision_streaming = False
            self.restore_head_motion_after_vision(True)

    def camera_dimensions(self, resolution):
        if resolution == 2:
            return 640, 480
        if resolution == 1:
            return 320, 240
        if resolution == 0:
            return 160, 120

        return 320, 240

    def pause_head_motion_for_vision(self):
        if self.vision_motion_paused:
            return

        try:
            if self.basic_awareness:
                self.awareness_was_enabled = self.basic_awareness.isEnabled()

                if self.awareness_was_enabled:
                    self.basic_awareness.setEnabled(False)
                    self.logger.info("Basic awareness paused for vision capture.")

        except Exception as e:
            self.logger.warning("Could not pause basic awareness for vision: " + str(e))

        try:
            if self.motion:
                original_head_angles = self.motion.getAngles(
                    ["HeadYaw", "HeadPitch"],
                    True
                )
                self.head_angles_before_vision = [PEPPER_HEAD_YAW, PEPPER_HEAD_PITCH]

                current_stiffness = self.motion.getStiffnesses(
                    ["HeadYaw", "HeadPitch"]
                )

                self.head_was_stiff = any(s > 0.1 for s in current_stiffness)

                self.motion.setStiffnesses(
                    ["HeadYaw", "HeadPitch"],
                    1.0
                )

                self.motion.setAngles(
                    ["HeadYaw", "HeadPitch"],
                    self.head_angles_before_vision,
                    0.15
                )

                self.logger.info(
                    "Head moved from %s to forward vision pose %s."
                    % (original_head_angles, self.head_angles_before_vision)
                )

        except Exception as e:
            self.logger.warning("Could not lock head for vision: " + str(e))

        self.vision_motion_paused = True
        self.last_head_lock_refresh = time.time()

    def refresh_head_lock_for_vision(self):
        if not self.vision_motion_paused or not self.motion or not self.head_angles_before_vision:
            return
        now = time.time()
        if now - self.last_head_lock_refresh < 2.0:
            return
        try:
            self.motion.setAngles(
                ["HeadYaw", "HeadPitch"],
                self.head_angles_before_vision,
                0.12
            )
            self.last_head_lock_refresh = now
        except Exception as e:
            self.logger.warning("Could not refresh head lock for vision: " + str(e))

    def restore_head_motion_after_vision(self, force=False):
        if not self.vision_motion_paused:
            return
        if self.vision_streaming and not force:
            return

        try:
            if self.motion:
                if self.head_angles_before_vision:
                    self.motion.setAngles(
                        ["HeadYaw", "HeadPitch"],
                        self.head_angles_before_vision,
                        0.15
                    )

                if not self.head_was_stiff:
                    self.motion.setStiffnesses(
                        ["HeadYaw", "HeadPitch"],
                        0.0
                    )

                self.logger.info("Head motion restored after vision capture.")

        except Exception as e:
            self.logger.warning("Could not restore head motion after vision: " + str(e))

        try:
            if self.basic_awareness and self.awareness_was_enabled:
                self.basic_awareness.setEnabled(True)
                self.logger.info("Basic awareness restored after vision capture.")

        except Exception as e:
            self.logger.warning("Could not restore basic awareness after vision: " + str(e))

        self.awareness_was_enabled = None
        self.head_was_stiff = None
        self.head_angles_before_vision = None
        self.last_head_lock_refresh = 0.0
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
        if not self.audio_socket:
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
