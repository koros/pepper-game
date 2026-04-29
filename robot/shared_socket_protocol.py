# -*- coding: utf-8 -*-
"""
Small Python 2/3 compatible socket helpers.

Messages are newline-terminated JSON objects. Binary audio and image payloads
use a fixed-size header followed by raw bytes.
"""

from __future__ import print_function

import json
import socket
import struct


ENCODING = "utf-8"
HEADER_STRUCT = "!I"
IMAGE_HEADER_STRUCT = "!III"


def ensure_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode(ENCODING)


def ensure_text(value):
    if isinstance(value, str):
        return value
    return value.decode(ENCODING)


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_json(sock, payload):
    line = json.dumps(payload) + "\n"
    sock.sendall(ensure_bytes(line))


def recv_json_line(sock):
    chunks = []
    while True:
        char = sock.recv(1)
        if not char:
            return None
        if char == b"\n" or char == "\n":
            break
        chunks.append(char)
    raw = b"".join(chunks)
    return json.loads(ensure_text(raw))


def send_blob(sock, data):
    sock.sendall(struct.pack(HEADER_STRUCT, len(data)) + data)


def recv_blob(sock):
    header = recv_exact(sock, 4)
    if header is None:
        return None
    size = struct.unpack(HEADER_STRUCT, header)[0]
    return recv_exact(sock, size)


def send_image(sock, width, height, data):
    header = struct.pack(IMAGE_HEADER_STRUCT, width, height, len(data))
    sock.sendall(header + data)


def recv_image(sock):
    header = recv_exact(sock, 12)
    if header is None:
        return None
    width, height, size = struct.unpack(IMAGE_HEADER_STRUCT, header)
    data = recv_exact(sock, size)
    if data is None:
        return None
    return width, height, data


def connect(host, port, timeout=10.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))
    return sock
