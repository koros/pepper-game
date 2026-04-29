# -*- coding: utf-8 -*-


PC_IP = "127.0.0.1"
AUDIO_INPUT_MODE = "pc"   # "pepper" or "pc"
VISION_INPUT_MODE = "pc"  # "pepper" or "pc"
SPEECH_OUTPUT_MODE = "pc" # "pepper" or "pc"


class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self, False)

    def onLoad(self):
        pass

    def onUnload(self):
        pass

    def onInput_onStart(self):
        self.logger.info("PC_IP=" + str(PC_IP))
        self.logger.info("AUDIO_INPUT_MODE=" + str(AUDIO_INPUT_MODE))
        self.logger.info("VISION_INPUT_MODE=" + str(VISION_INPUT_MODE))
        self.logger.info("SPEECH_OUTPUT_MODE=" + str(SPEECH_OUTPUT_MODE))
        self.onStopped()

    def onInput_onStop(self):
        self.onStopped()
