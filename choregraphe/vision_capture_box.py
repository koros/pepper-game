# -*- coding: utf-8 -*-
import json
import socket
import struct
import time


PC_IP = "127.0.0.1"
COMMAND_PORT = 50010
VISION_PORT = 50012
VISION_INPUT_MODE = "pc"  # "pepper" or "pc"
VISION_HEAD_STILL_SECONDS = 6.0


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)
        self.video = None
        self.basic_awareness = None
        self.subscriber_id = None
        self.bIsRunning = False
        self.awareness_was_enabled = None
        self.vision_motion_paused = False

    def onLoad(self):
        self.bIsRunning = False
        self.logger.info("Vision capture box loaded with VISION_INPUT_MODE=" + str(VISION_INPUT_MODE))
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
        self.logger.info("Vision capture box unloading.")
        self.bIsRunning = False
        self.restore_head_motion_after_vision()
        if self.video and self.subscriber_id:
            try:
                self.video.unsubscribe(self.subscriber_id)
            except Exception:
                pass
            self.subscriber_id = None
        while self.bIsRunning:
            time.sleep(0.2)

    def onInput_onStart(self):
        self.bIsRunning = True
        self.logger.info("Vision capture box starting.")
        try:
            if VISION_INPUT_MODE == "pc":
                self.logger.info("Delegating vision capture to PC by manual flag.")
                self.request_pc_vision()
            elif VISION_INPUT_MODE == "pepper":
                self.logger.info("Capturing vision with Pepper camera by manual flag.")
                self.capture_pepper_vision()
            else:
                self.logger.error("Invalid VISION_INPUT_MODE: " + str(VISION_INPUT_MODE))
        except Exception as e:
            self.logger.error("Vision capture failed: " + str(e))
        finally:
            self.onInput_onStop()

    def request_pc_vision(self):
        self.logger.info("Connecting command socket for pc_vision_check.")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.pause_head_motion_for_vision()
            sock.connect((PC_IP, COMMAND_PORT))
            sock.sendall(json.dumps({"event": "pc_vision_check"}) + "\n")
            self.logger.info("pc_vision_check command sent.")
            time.sleep(VISION_HEAD_STILL_SECONDS)
        finally:
            sock.close()
            self.restore_head_motion_after_vision()

    def capture_pepper_vision(self):
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
            self.logger.info("Pepper camera active. Subscribing for one frame.")
            self.subscriber_id = self.video.subscribeCamera(
                "cup_game_vision", camera_index, resolution, color_space, fps
            )
            image = self.video.getImageRemote(self.subscriber_id)
            if image is None:
                self.logger.error("No image received from Pepper camera.")
                return

            data = image[6]
            self.logger.info("Captured Pepper frame bytes=" + str(len(data)))
            header = struct.pack("!III", width, height, len(data))
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.logger.info("Connecting vision socket to " + str(PC_IP) + ":" + str(VISION_PORT))
                sock.connect((PC_IP, VISION_PORT))
                sock.sendall(header + data)
                self.logger.info("Pepper frame sent to PC vision socket.")
            finally:
                sock.close()
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

    def onInput_onStop(self):
        self.logger.info("Vision capture box stopping.")
        self.onUnload()
        self.onStopped()
