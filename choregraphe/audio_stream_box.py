# -*- coding: utf-8 -*-
import json
import socket
import struct
import time


PC_IP = "127.0.0.1"
COMMAND_PORT = 50010
AUDIO_PORT = 50011
AUDIO_INPUT_MODE = "pc"  # "pepper" or "pc"
AUDIO_SUBSCRIBER_NAME = "Audio_2"


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)
        self.audio = None
        self.command_socket = None
        self.audio_socket = None
        self.bIsRunning = False

    def onLoad(self):
        self.bIsRunning = False
        self.logger.info("Audio stream box loaded with AUDIO_INPUT_MODE=" + str(AUDIO_INPUT_MODE))
        self.logger.info("AUDIO_SUBSCRIBER_NAME=" + str(AUDIO_SUBSCRIBER_NAME))
        try:
            self.audio = ALProxy("ALAudioDevice")
            self.logger.info("ALAudioDevice proxy ready.")
        except Exception as e:
            self.logger.warning("ALAudioDevice unavailable: " + str(e))
            self.audio = None

    def onUnload(self):
        self.logger.info("Audio stream box unloading.")
        self.bIsRunning = False
        try:
            if self.audio:
                self.audio.unsubscribe(self.audio_subscriber_name())
        except Exception:
            pass
        self.close_socket(self.command_socket)
        self.close_socket(self.audio_socket)
        while self.bIsRunning:
            time.sleep(0.2)

    def onInput_onStart(self):
        self.bIsRunning = True
        self.logger.info("Audio stream box starting.")
        if AUDIO_INPUT_MODE == "pc":
            self.logger.info("Delegating audio capture to PC by manual flag.")
            self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.command_socket.connect((PC_IP, COMMAND_PORT))
            self.command_socket.sendall(json.dumps({"event": "pc_audio_turn"}) + "\n")
            self.onStopped()
            return

        if AUDIO_INPUT_MODE != "pepper":
            self.logger.error("Invalid AUDIO_INPUT_MODE: " + str(AUDIO_INPUT_MODE))
            self.onInput_onStop()
            return

        if not self.audio:
            self.logger.error("AUDIO_INPUT_MODE is pepper, but ALAudioDevice is unavailable.")
            self.onInput_onStop()
            return

        self.audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.logger.info("Connecting audio socket to " + str(PC_IP) + ":" + str(AUDIO_PORT))
        self.audio_socket.connect((PC_IP, AUDIO_PORT))
        subscriber_name = self.audio_subscriber_name()
        self.audio.setClientPreferences(subscriber_name, 16000, 3, 0)
        self.audio.subscribe(subscriber_name)
        self.logger.info(
            "Pepper microphone subscribed and streaming with subscriber name=%s."
            % subscriber_name
        )

    def audio_subscriber_name(self):
        return str(AUDIO_SUBSCRIBER_NAME)

    def processRemote(self, nbOfChannels, nbrOfSamplesByChannel, timestamp, buffer):
        if not self.bIsRunning or AUDIO_INPUT_MODE != "pepper":
            return
        try:
            self.audio_socket.sendall(struct.pack("!I", len(buffer)) + buffer)
        except Exception as e:
            self.logger.error("Audio send failed: " + str(e))
            self.onInput_onStop()

    def close_socket(self, sock):
        try:
            if sock:
                sock.close()
        except Exception:
            pass

    def onInput_onStop(self):
        self.logger.info("Audio stream box stopping.")
        self.onUnload()
        self.onStopped()
