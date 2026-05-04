# Communication Between Pepper and the PC Server

## Executive Summary

This Pepper cup game uses a client-server architecture. Pepper runs the Choregraphe Python entry point, while the PC runs a Python server called `CupGameOrchestrator`. Pepper is responsible for robot-side interaction with NAOqi services such as text-to-speech, microphone capture, camera capture, head motion, and basic awareness. The PC server is responsible for the heavier tasks: coordinating the game state, interpreting commands, capturing or receiving audio and vision data, running cup detection, transcribing speech, deciding when to offer hints, and sending responses back to Pepper.

Communication happens over plain TCP sockets. The design separates traffic by purpose instead of using one mixed channel. There is one socket for command events, one for Pepper audio data, one for Pepper camera frames, and one for server results. This makes the system easier to reason about because low-rate JSON events do not compete with high-volume binary audio or image payloads.

In the current repository configuration, the PC is set to handle audio input, vision input, and speech output. However, the same code also supports Pepper-side microphone streaming, Pepper-side camera frames, and Pepper speech output when the flags are changed on both sides.

## Main Components

### Pepper / Choregraphe side

The robot-side entry point is `choregraphe/cup_game_entry_box.py`. It is written in the Choregraphe `GeneratedClass` style and runs inside Pepper's Python/NAOqi environment. On load, it creates proxies to NAOqi services:

- `ALTextToSpeech` for Pepper speech output.
- `ALAudioDevice` for Pepper microphone streaming.
- `ALVideoDevice` for Pepper camera frames.
- `ALBasicAwareness` and `ALMotion` for stabilising the robot's head during vision capture.

When the Choregraphe box starts, Pepper connects to the server, sends startup commands, starts optional background streams, and chooses the input mode based on manual flags.

### PC server side

The PC-side coordinator is `server/pc_orchestrator.py`. It starts four background listener threads:

- `command_loop` for command/control events.
- `audio_loop` for Pepper microphone audio.
- `vision_loop` for Pepper camera frames.
- `result_loop` for sending replies back to Pepper.

The orchestrator then delegates work to supporting services:

- `server/cup_game_logic.py` handles game state and checking guesses.
- `server/vision_service.py` handles OpenCV camera capture and cup detection.
- `server/audio_service.py` handles PC microphone capture, voice activity detection, and Whisper transcription.
- `server/speech_service.py` handles PC text-to-speech fallback.
- `server/lm_studio_client.py` optionally rewrites responses into short Pepper-friendly text.
- `robot/pepper_pomdp.py` is imported by the PC server to decide when a hint should be offered.

## Network Configuration

The socket ports are configured in `config/ports.json`:

| Port | Channel | Direction | Purpose |
|---:|---|---|---|
| `50010` | Command socket | Pepper to PC, with fallback PC to Pepper | Sends JSON control events such as `hello`, `start_round`, `pc_vision_check`, and `speech_done`. |
| `50011` | Audio socket | Pepper to PC | Streams raw Pepper microphone audio as binary chunks when Pepper audio mode is enabled. |
| `50012` | Vision socket | Pepper to PC | Sends Pepper camera frames as binary image payloads when Pepper vision mode is enabled. |
| `50013` | Result socket | PC to Pepper | Sends JSON result events containing response text and structured data. |

The PC server listens on these ports. Pepper acts as the client and connects to the PC using the configured `PC_IP`. In the Choregraphe script, the default `PC_IP` is currently `127.0.0.1`, which is useful for local testing but must be changed to the real PC IP when running on the physical robot.

## Message Encoding Protocol

The project uses two encodings: newline-delimited JSON for events and length-prefixed binary payloads for media data.

### JSON events

Control and result messages are encoded as JSON objects followed by a newline character:

```text
{"event": "start_round"}\n
```

This is used by the command and result sockets. The newline acts as the message boundary, so the receiver reads bytes until it sees `\n`, decodes the accumulated bytes as UTF-8, and parses the result as JSON.

Typical command payloads:

```json
{"event": "hello"}
{"event": "start_round"}
{"event": "pc_vision_check"}
{"event": "pepper_vision_check"}
{"event": "pc_audio_turn"}
{"event": "speech_done", "speech_event": "round_started", "speech_token": "...", "text": "..."}
```

Typical result payloads:

```json
{"event": "ready", "text": "PC cup game server is ready.", "data": {}}
{"event": "round_started", "text": "...", "data": {"target_length": 3, "max_attempts": 15}}
{"event": "vision_requested", "text": "Checking the Pepper camera frame.", "data": {}}
{"event": "vision_checked", "text": "...", "data": {"top_row": ["purple", "yellow", "green"]}}
{"event": "hint", "text": "...", "data": {...}}
```

### Binary audio

Pepper audio uses a 4-byte unsigned integer header followed by raw audio bytes:

```text
[4-byte size][PCM audio bytes]
```

The integer is packed in network byte order using the struct format `!I`. The PC reads the size first, then reads exactly that many bytes. This prevents partial TCP reads from corrupting message boundaries.

### Binary images

Pepper camera frames use a 12-byte header followed by raw RGB image bytes:

```text
[4-byte width][4-byte height][4-byte size][RGB frame bytes]
```

The header is packed as `!III`, again using network byte order. The PC uses the width and height to decode the frame and the size field to read the exact image payload.

## Startup Flow

The server is started first with:

```bash
python server/pc_orchestrator.py
```

At startup, the PC logs its local IP address, the configured ports, and the active audio/vision/speech modes. It then starts four daemon threads for command, audio, vision, and result handling.

When the Choregraphe box starts on Pepper:

1. Pepper opens the command socket to the PC on port `50010`.
2. Pepper opens the result socket to the PC on port `50013`.
3. Pepper starts a result-listener thread so it can receive server responses continuously.
4. Pepper sends `{"event": "hello"}`.
5. Pepper sends `{"event": "start_round"}`.
6. The PC replies with a `ready` result and starts the game round.
7. The PC sends a `round_started` result containing the opening instructions.
8. Depending on the configured mode, Pepper either requests PC input/capture or sends its own audio/camera data.

This startup design means Pepper does not need to know the complete game logic. It simply signals lifecycle events and follows the server's response events.

## Current Runtime Mode

The current `config/runtime_flags.json` and Choregraphe constants are aligned to use PC-side input/output:

```json
{
  "audio_input_mode": "pc",
  "vision_input_mode": "pc",
  "speech_output_mode": "pc",
  "player_input_mode": "vision"
}
```

With these settings:

- Pepper still connects to the PC server and starts the round.
- Pepper does not stream microphone audio.
- Pepper does not send a camera frame for checks.
- Pepper sends `pc_vision_check`, and the PC captures from its own camera.
- Server replies are spoken or printed by the PC speech service instead of being spoken by Pepper.

This mode is useful for testing because the game can run without relying on Pepper's physical microphone, camera, or TTS. For a full robot demonstration, the flags can be changed to `pepper` so that Pepper provides the input/output surfaces while the PC still performs the processing.

## Vision Communication Flow

There are two supported vision paths.

### PC vision mode

In PC vision mode, Pepper sends a command event:

```json
{"event": "pc_vision_check"}
```

The PC receives this command on the command socket and calls `handle_pc_vision_check`. The server then captures an image from the PC webcam using `VisionService`, runs cup detection, evaluates the detected sequence against the game logic, and sends a `vision_checked` result back.

The data flow is:

```text
Pepper -> command socket -> PC orchestrator -> PC webcam -> vision service -> game logic -> result socket -> Pepper/PC speech
```

### Pepper vision mode

In Pepper vision mode, Pepper first sends:

```json
{"event": "pepper_vision_check"}
```

The PC marks the next incoming Pepper frame as the frame that should be evaluated. Pepper then captures a camera frame through `ALVideoDevice` and sends it to port `50012` using the image binary protocol. When the PC receives the frame, it checks whether a Pepper vision request is currently armed. If yes, it evaluates the frame. If no, it treats the frame as a preview frame.

The data flow is:

```text
Pepper ALVideoDevice -> vision socket -> PC orchestrator -> vision service -> game logic -> result socket -> Pepper
```

Pepper also pauses basic awareness and locks the head angle before capture. This reduces motion blur and keeps the table/cups in a stable camera view. After the check result is received, Pepper restores the head and awareness state.

## Audio Communication Flow

There are also two supported audio paths.

### PC audio mode

In PC audio mode, Pepper sends:

```json
{"event": "pc_audio_turn"}
```

The PC then listens through its own microphone. The audio service uses energy-based voice activity detection and Whisper transcription. Once text is recognised, the server classifies it as a game command such as `check`, `hint`, or a replay answer. The resulting action is handled entirely on the PC server.

The current PC control mode is `hybrid`, which means the PC can accept either speech or keyboard shortcuts such as `C` for check, `H` for help, `Y` for replay yes, and `N` for replay no.

### Pepper audio mode

In Pepper audio mode, Pepper connects to the audio socket on port `50011`, subscribes to `ALAudioDevice`, and registers a remote audio callback module. Every time Pepper receives microphone audio in `processRemote`, it sends the buffer as a length-prefixed blob.

The PC server receives each blob in `audio_loop` and feeds it into an utterance buffer. When enough speech has been collected and silence indicates the utterance is complete, the PC transcribes the PCM audio and passes the recognised text into the same spoken-input handler used by PC audio mode.

The data flow is:

```text
Pepper ALAudioDevice -> audio socket -> PC utterance buffer -> Whisper transcription -> command classification -> game logic
```

The Choregraphe script currently has `PEPPER_AUDIO_STREAM_ENABLED = False`, so if Pepper audio mode is selected, it deliberately falls back to PC microphone input.

## Result and Speech Flow

All server responses are normalised into result events:

```json
{
  "event": "vision_checked",
  "text": "You matched two cups. Try again.",
  "data": {
    "status": "in_progress",
    "attempt": 2
  }
}
```

The PC sends these events primarily over the result socket on port `50013`. If that socket is unavailable, the server tries to send over the command socket as a fallback.

If `speech_output_mode` is `pc`, the PC speaks the text locally using `SpeechService`. The result event is still sent, but Pepper logs it rather than speaking it.

If `speech_output_mode` is `pepper`, the server sends the result text to Pepper. Pepper's result-listener thread receives it, calls `ALTextToSpeech.post.say`, waits for the speech job to complete, and then sends a `speech_done` command back to the PC. This acknowledgement matters when the PC is about to start listening through its own microphone: the server can wait until Pepper has finished speaking so the PC microphone does not accidentally capture Pepper's voice as user input.

The speech-completion handshake uses a `speech_token`. The server includes the token in the result data, Pepper sends the same token back in the `speech_done` event, and the server unblocks the waiting input flow.

## Game-Level Communication Sequence

A typical check cycle in vision mode looks like this:

1. The PC prompts the player: press or say check when ready.
2. The user gives the check command.
3. If PC vision is active, the server captures a PC webcam frame.
4. If Pepper vision is active, Pepper sends `pepper_vision_check`, captures a robot camera frame, and sends it to the vision socket.
5. The PC vision service detects the top row and bottom row of cups.
6. The bottom row can become the target sequence if it is detected clearly.
7. The top row is evaluated as the player's current arrangement.
8. The PC game logic produces an outcome: correct, partially correct, unclear, finished, or needs another attempt.
9. The PC sends a `vision_checked` result with user-facing text and structured outcome data.
10. The output system speaks or logs the result.
11. If the game is not finished, the PC prompts for the next check or hint.

## Threading Model

The PC server is deliberately multi-threaded. Each socket channel has its own accept loop. Incoming command events are also dispatched onto short-lived handler threads. Vision result handling from Pepper frames is put into a separate thread so that the vision socket remains available for preview frames while the server is speaking or waiting for user input.

Pepper also uses a background thread for result listening. This allows Pepper to receive server replies while the main Choregraphe box is still managing capture, speech, or cleanup.

This threading model is important because robot interaction is asynchronous. Speech can take several seconds, camera frames can arrive repeatedly, and users may delay before responding. Separate threads prevent one slow activity from blocking all communication.

## Reliability and Error Handling

Several design choices improve reliability:

- TCP is used for every channel, so data arrives in order and without missing bytes.
- JSON messages are newline-delimited, giving simple and reliable event boundaries.
- Binary data is length-prefixed, so the receiver knows exactly how many bytes to read.
- The server uses `SO_REUSEADDR` so sockets can be restarted more easily after a crash or development run.
- Pepper closes sockets and unsubscribes from NAOqi audio/video services during unload.
- Pepper restores head motion and basic awareness after vision capture.
- Pepper audio can fall back to PC audio if streaming is disabled or unavailable.
- Server responses prefer the result socket but can fall back to the command socket.
- Pepper speech completion is acknowledged with `speech_done` and a token, preventing the PC from listening too early.

The main limitation is that the protocol is custom and local-network oriented. There is no authentication, encryption, or reconnection protocol beyond closing and reopening sockets. This is acceptable for a controlled lab demonstration, but a production robot deployment would need stronger connection management and security.

## Why This Architecture Works Well for Pepper

Pepper has limited onboard compute and runs an older Python/NAOqi environment, while the PC can run modern Python packages such as OpenCV, faster-whisper, and LM Studio clients. The architecture therefore keeps Pepper's role focused on embodiment: sensing, speaking, and moving. The PC handles computationally heavier and easier-to-debug tasks.

This separation also makes development easier. The PC-side services can be tested locally using PC audio, PC camera, and PC speech before deploying the full behavior to Pepper. Once the game logic is stable, the manual flags can move the physical input/output back to Pepper without changing the core orchestration.

## Presentation-Friendly Summary

Pepper and the server communicate through four TCP sockets: commands, audio, vision, and results. Commands and results are newline-delimited JSON events. Audio and image data are binary payloads with fixed-size headers so the server can reconstruct complete messages. Pepper starts the game by connecting to the command and result sockets, sending `hello` and `start_round`, and then requesting either PC-side or Pepper-side input depending on the runtime flags. The PC server coordinates all game logic, vision recognition, speech transcription, hint policy, and response generation. Results are sent back as structured JSON events, and Pepper either speaks the text itself or lets the PC speak depending on the selected speech mode.

In short, Pepper acts as the embodied interface, while the PC server acts as the brain of the game.
