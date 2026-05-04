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
|   |-- lm_studio_client.py
|   `-- fixed_slot_detector/
|-- choregraphe/
|   `-- cup_game_entry_box.py
`-- robot/
    `-- pepper_pomdp.py
```

## Socket Ports

Configured in `config/ports.json`.

```text
50010 command/control socket
50011 Pepper audio stream to PC
50012 Pepper camera frame to PC
50013 PC result text to Pepper
```

## Runtime Modes

The user controls delegation manually. Pepper does not switch modes by itself during the game. Keep the PC config and Choregraphe flags aligned before running.

PC-side defaults live in `config/runtime_flags.json`:

```json
{
  "audio_input_mode": "pc",
  "vision_input_mode": "pc",
  "speech_output_mode": "pc"
}
```

The Choregraphe Python box has matching flags near the top:

```python
AUDIO_INPUT_MODE = "pc"       # "pepper" or "pc"
VISION_INPUT_MODE = "pc"      # "pepper" or "pc"
SPEECH_OUTPUT_MODE = "pc"     # "pepper" or "pc"
PLAYER_INPUT_MODE = "vision"  # "vision" or "speech"
```

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

## PC Server Setup

Install Python 3.8 to 3.11, then set up the project:

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

Keep this server running before you start the Choregraphe behavior.

## Choregraphe Setup

Use these steps for both PC mode and Pepper mode.

1. Open Choregraphe.
2. Connect Choregraphe to the target:
   - For PC mode, connect to a virtual robot.
   - For Pepper mode, connect to the actual Pepper robot.
3. Create a new behavior or open your behavior project.
4. Drag a new Python Script box into the behavior workspace.
5. Double-click the Python box to edit it.
6. Copy all code from `choregraphe/cup_game_entry_box.py`.
7. Paste that code into the Choregraphe Python Script box, replacing the template code.
8. Rename the box if desired. The screenshot uses `Game`.
9. Wire the box as shown in the screenshot:
   - Connect the root input on the left to the Python box input.
   - Connect the Python box output to the root output on the right.
10. Update the settings at the top of the Python script.
11. Make the matching changes in `config/runtime_flags.json` on the PC.
12. Start the PC server, then press Play in Choregraphe.

### PC Mode With A Virtual Robot

Use this mode for local testing. Choregraphe is connected to a virtual robot, and the PC handles microphone input, camera input, and speech output.

In `choregraphe/cup_game_entry_box.py`, use:

```python
PC_IP = "127.0.0.1"
AUDIO_INPUT_MODE = "pc"
VISION_INPUT_MODE = "pc"
SPEECH_OUTPUT_MODE = "pc"
PLAYER_INPUT_MODE = "vision"
```

In `config/runtime_flags.json`, use:

```json
{
  "audio_input_mode": "pc",
  "vision_input_mode": "pc",
  "speech_output_mode": "pc",
  "player_input_mode": "vision",
  "vision_preview_enabled": true,
  "vision_live_preview_enabled": true
}
```

Run the PC server:

```bash
python server/pc_orchestrator.py
```

<img width="1448" height="813" alt="choregraphe" src="https://github.com/user-attachments/assets/ff6356f9-4c0b-440d-9da0-0c3c7f47f687" />


Then run the behavior in Choregraphe.

### Pepper Mode With A Real Robot

Use this mode when Choregraphe is connected to an actual Pepper robot. Pepper can provide microphone input, camera input, and spoken output, while the PC still runs the game server and AI services.

In the Choregraphe Python box, update `PC_IP` to the PC address Pepper can reach, then use:

```python
PC_IP = "YOUR_PC_IPV4_ADDRESS"
AUDIO_INPUT_MODE = "pepper"
VISION_INPUT_MODE = "pepper"
SPEECH_OUTPUT_MODE = "pepper"
PLAYER_INPUT_MODE = "vision"
```

In `config/runtime_flags.json`, use:

```json
{
  "audio_input_mode": "pepper",
  "vision_input_mode": "pepper",
  "speech_output_mode": "pepper",
  "player_input_mode": "vision",
  "vision_preview_enabled": true
}
```

Run the PC server, then upload and run the behavior from Choregraphe.

## Finding The PC IP Address

`PC_IP` must be the IPv4 address of the PC running `server/pc_orchestrator.py`.

For PC mode with a virtual robot on the same machine, use:

```python
PC_IP = "127.0.0.1"
```

For Pepper mode with a real robot, use the PC network interface connected to Pepper. A direct Ethernet connection to Pepper often uses an automatic private address in this range:

```text
169.254.x.x
```

On Windows, open Command Prompt or PowerShell and run:

```powershell
ipconfig
```

Look for the Ethernet adapter connected to Pepper, then copy its `IPv4 Address`. It should usually start with `169.254.` for the direct Pepper Ethernet link.

On macOS or Linux, run:

```bash
ifconfig
```

or:

```bash
ip addr
```

Use the matching IPv4 address that starts with `169.254.`. For example:

```python
PC_IP = "169.254.103.203"
```

Make sure Windows Firewall allows Python to accept incoming connections, or Pepper may fail to connect to the PC server.

## Running The Game

After the PC server is running and the Choregraphe box is wired:

1. Connect to the PC server.
2. Start a color-sequence game.
3. Listen for the user to say or press `check` or `hint`, depending on the configured input mode.
4. Capture both cup rows with vision when the user asks for a check.
5. Speak or log the result based on `SPEECH_OUTPUT_MODE`.

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

Vision uses the fixed-slot detector in `server/fixed_slot_detector/` to extract `top_row` and `bottom_row` sequences. Tune that detector for your actual cup colors, lighting, camera angle, and table setup.

## Vision Preview

The PC vision service can show the latest processed frame in an OpenCV window, like the original template.

Configure it in `config/runtime_flags.json`:

```json
{
  "vision_preview_enabled": true,
  "vision_window_name": "Pepper Cup Game Vision"
}
```

When the preview window is focused, press `q` to close it for the current server run.
