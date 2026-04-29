from __future__ import annotations

import json
import socket
import struct
from typing import Dict, Optional, Tuple


def recv_exact(sock: socket.socket, size: int) -> Optional[bytes]:
    chunks = []
    remaining = size
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except OSError:
            return None
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_json_line(sock: socket.socket) -> Optional[Dict[str, object]]:
    chunks = []
    while True:
        try:
            char = sock.recv(1)
        except OSError:
            return None
        if not char:
            return None
        if char == b"\n":
            break
        chunks.append(char)
    return json.loads(b"".join(chunks).decode("utf-8"))


def send_json(sock: socket.socket, payload: Dict[str, object]) -> None:
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def recv_blob(sock: socket.socket) -> Optional[bytes]:
    header = recv_exact(sock, 4)
    if header is None:
        return None
    size = struct.unpack("!I", header)[0]
    return recv_exact(sock, size)


def recv_image(sock: socket.socket) -> Optional[Tuple[int, int, bytes]]:
    header = recv_exact(sock, 12)
    if header is None:
        return None
    width, height, size = struct.unpack("!III", header)
    data = recv_exact(sock, size)
    if data is None:
        return None
    return width, height, data


def listen(host: str, port: int) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    return server


def local_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()
