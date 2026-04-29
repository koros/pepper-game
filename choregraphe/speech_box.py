# -*- coding: utf-8 -*-
import json
import socket
import threading
import time


PC_IP = "127.0.0.1"
RESULT_PORT = 50013
SPEECH_OUTPUT_MODE = "pc"  # "pepper" or "pc"


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)
        self.result_socket = None
        self.tts = None
        self.tts_stop = None
        self.thread = None
        self.bIsRunning = False
        self.speech_ids = []

    def onLoad(self):
        self.bIsRunning = False
        self.speech_ids = []
        self.logger.info("Speech box loaded with SPEECH_OUTPUT_MODE=" + str(SPEECH_OUTPUT_MODE))
        try:
            self.tts = ALProxy("ALTextToSpeech")
            self.tts_stop = ALProxy("ALTextToSpeech", True)
            self.logger.info("ALTextToSpeech proxy ready.")
        except Exception as e:
            self.logger.warning("TTS unavailable: " + str(e))

    def onUnload(self):
        self.logger.info("Speech box unloading.")
        self.bIsRunning = False
        for speech_id in self.speech_ids:
            try:
                self.tts_stop.stop(speech_id)
            except Exception:
                pass
        try:
            if self.result_socket:
                self.result_socket.close()
        except Exception:
            pass

    def onInput_onStart(self):
        self.bIsRunning = True
        self.logger.info("Speech box starting. Connecting result socket.")
        self.result_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.result_socket.connect((PC_IP, RESULT_PORT))
        self.logger.info("Result socket connected.")
        self.thread = threading.Thread(target=self.listen_loop)
        self.thread.daemon = True
        self.thread.start()

    def recv_json_line(self):
        chunks = []
        while self.bIsRunning:
            char = self.result_socket.recv(1)
            if not char:
                return None
            if char == "\n":
                break
            chunks.append(char)
        return json.loads("".join(chunks))

    def listen_loop(self):
        while self.bIsRunning:
            try:
                message = self.recv_json_line()
                if message is None:
                    return
                text = str(message.get("text", ""))
                self.logger.info("PC result: " + text)
                if SPEECH_OUTPUT_MODE == "pepper" and self.tts and text:
                    speech_id = self.tts.post.say(text)
                    self.logger.info("Started Pepper TTS job id=" + str(speech_id))
                    self.speech_ids.append(speech_id)
                    self.tts.wait(speech_id, 0)
            except Exception as e:
                self.logger.error("Speech listener stopped: " + str(e))
                return
            time.sleep(0.1)

    def onInput_onStop(self):
        self.onUnload()
        self.onStopped()
