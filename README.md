# Pepper Cup Game Choregraphe

This project runs a Pepper cup game with Choregraphe as the robot-side entry point and a Python 3 PC server handling heavier work through sockets.

The project follows the style of the template projects:

- Pepper/Choregraphe scripts use `class MyClass(GeneratedClass):`.
- Pepper talks to the PC through TCP sockets.
- The user manually chooses whether Pepper or the PC handles audio, vision, and speech.

## Folder Structure

```text
pepper_cup_game_choregraphe/
|-- README.md
|-- requirements.txt
|-- config/
|   |-- ports.json
|   `-- runtime_flags.json
|-- server/
|   |-- pc_orchestrator.py
|   |-- sockets.py
|   |-- audio_service.py
|   |-- vision_service.py
|   |-- cup_game_logic.py
|   `-- lm_studio_client.py
|-- choregraphe/
|   |-- cup_game_entry_box.py
|   |-- audio_stream_box.py
|   |-- vision_capture_box.py
|   |-- speech_box.py
|   `-- config_box.py
`-- robot/
    `-- shared_socket_protocol.py
```

## Socket Ports

Configured in `config/ports.json`.

```text
50010 command/control socket
50011 Pepper audio stream to PC
50012 Pepper camera frame to PC
50013 PC result text to Pepper
```

## Manual Delegation Flags

The user controls delegation manually. Pepper does not switch modes by itself during the game.

PC-side defaults live in `config/runtime_flags.json`:

```json
{
  "audio_input_mode": "pepper",
  "vision_input_mode": "pepper",
  "speech_output_mode": "pepper"
}
```

Choregraphe scripts also have matching flags at the top:

```python
AUDIO_INPUT_MODE = "pepper"   # "pepper" or "pc"
VISION_INPUT_MODE = "pepper"  # "pepper" or "pc"
SPEECH_OUTPUT_MODE = "pepper" # "pepper" or "pc"
```

Keep the PC config and Choregraphe flags aligned before running.

## Audio Detection On Windows

This project does not use `webrtcvad`, because that package can be difficult to install on Windows.

Instead, the PC server uses a lightweight energy-based VAD implemented with `numpy`. Tune it in `config/runtime_flags.json`:

```json
{
  "energy_vad_threshold": 550,
  "energy_vad_min_speech_frames": 3,
  "energy_vad_end_silence_frames": 18
}
```

If the server detects too much background noise as speech, increase `energy_vad_threshold`.

If it misses quiet speech, decrease `energy_vad_threshold`.

## Modes

### Audio

- `AUDIO_INPUT_MODE = "pepper"`: Pepper streams microphone frames to the PC on port `50011`.
- `AUDIO_INPUT_MODE = "pc"`: Pepper sends a command to the PC, and the PC uses its own microphone.

### Vision

- `VISION_INPUT_MODE = "pepper"`: Pepper captures one camera frame and sends it to the PC on port `50012`.
- `VISION_INPUT_MODE = "pc"`: Pepper sends a command to the PC, and the PC uses its own webcam.

### Speech

- `SPEECH_OUTPUT_MODE = "pepper"`: PC sends response text to Pepper on port `50013`, and Pepper speaks it.
- `SPEECH_OUTPUT_MODE = "pc"`: PC prints the response locally for emulator/testing flows.

## PC Setup

Install Python 3.8 to 3.11, then run:

```bash
cd pepper_cup_game_choregraphe
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Start LM Studio, load a model, and enable the local server at:

```text
http://localhost:1234/v1
```

Start the PC server:

```bash
python server/pc_orchestrator.py
```

The server prints the PC IP address to use in Choregraphe.

## Choregraphe Setup

Paste `choregraphe/cup_game_entry_box.py` into a Choregraphe Python box.

At the top of the script, set:

```python
PC_IP = "YOUR_PC_IP"
AUDIO_INPUT_MODE = "pepper"
VISION_INPUT_MODE = "pepper"
SPEECH_OUTPUT_MODE = "pepper"
PLAYER_INPUT_MODE = "speech"
```

Run the box. Pepper will:

1. Connect to the PC server.
2. Start a color-sequence game.
3. Listen for the user to say `ready`, `check`, or `hint`.
4. Capture both cup rows with vision when the user says `ready` or `check`.
5. Speak or log the result based on `SPEECH_OUTPUT_MODE`.

## Optional Modular Boxes

The files below are provided if you prefer wiring separate Choregraphe boxes:

- `audio_stream_box.py`
- `vision_capture_box.py`
- `speech_box.py`
- `config_box.py`

For the simplest workflow, start with `cup_game_entry_box.py`.

## Cup Game Logic

The PC runs a two-row color sequence game:

- The target sequence is configured in `config/runtime_flags.json`.
- If vision sees the bottom row clearly, the detected bottom row becomes the target for that check.
- The player arranges the top row from left to right.
- The PC reports only exact matches by default, meaning correct color in the correct column.
- The PC keeps partial-match counts in structured data, but does not announce them unless you add a hint policy that uses them.
- If the user says `hint`, Pepper gives limited help based on the latest check.
- The PC asks LM Studio to phrase a short Pepper-friendly response.
- Pepper says the result.

Example config:

```json
{
  "target_sequence": ["red", "blue", "orange", "yellow"],
  "allowed_colors": ["red", "blue", "orange", "yellow", "green", "purple"],
  "max_attempts": 8
}
```

Vision currently splits the frame into upper and lower horizontal bands, then uses OpenCV HSV color masks to extract `top_row` and `bottom_row` sequences. You can tune `server/vision_service.py` for your actual cup colors, lighting, camera angle, and table setup.

## Vision Preview And Saving Frames

The PC vision service can show the latest processed frame in an OpenCV window, like the original template.

Configure it in `config/runtime_flags.json`:

```json
{
  "vision_preview_enabled": true,
  "vision_save_dir": "captured_frames",
  "vision_window_name": "Pepper Cup Game Vision"
}
```

When the preview window is focused:

- Press `s` to save the currently displayed frame.
- Press `q` to close the preview window for the current server run.

Saved frames are written under `captured_frames/` in this project.
