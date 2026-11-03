"""Small client for the local Codex Desktop owner channel.

This is an undocumented, version-sensitive Desktop protocol.  Only owner
discovery and user-turn submission are used here.  The Desktop process remains
the sole writer of every answer turn.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import time
import uuid
from pathlib import Path


SOCKET_PATH = Path.home() / ".codex" / "ipc" / "ipc.sock"
MAX_FRAME = 8 * 1024 * 1024


class DesktopIpcError(RuntimeError):
    pass


class DesktopIpc:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        info = SOCKET_PATH.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise DesktopIpcError("Codex Desktop socket has an unexpected owner or type")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(SOCKET_PATH))
        self.client_id = "initializing-client"
        result, _ = self._request("initialize", {"clientType": "futou_bot"}, 0)
        self.client_id = str(result.get("clientId") or "")
        if not self.client_id:
            raise DesktopIpcError("Codex Desktop did not assign a client ID")

    def __enter__(self) -> "DesktopIpc":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.sock.close()

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if not 0 < len(data) <= MAX_FRAME:
            raise DesktopIpcError("Codex Desktop request size is invalid")
        self.sock.sendall(struct.pack("<I", len(data)) + data)

    def _recv_exact(self, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            part = self.sock.recv(count - len(chunks))
            if not part:
                raise DesktopIpcError("Codex Desktop closed its socket")
            chunks.extend(part)
        return bytes(chunks)

    def _recv(self) -> dict:
        length = struct.unpack("<I", self._recv_exact(4))[0]
        if not 0 < length <= MAX_FRAME:
            raise DesktopIpcError("Codex Desktop returned an invalid frame")
        value = json.loads(self._recv_exact(length))
        if not isinstance(value, dict):
            raise DesktopIpcError("Codex Desktop returned a non-object frame")
        return value

    def _request(self, method: str, params: dict, version: int,
                 target: str | None = None) -> tuple[dict, dict]:
        request_id = str(uuid.uuid4())
        message = {"type": "request", "requestId": request_id,
                   "sourceClientId": self.client_id, "version": version,
                   "method": method, "params": params,
                   "timeoutMs": int(self.timeout * 1000)}
        if target:
            message["targetClientId"] = target
        self._send(message)
        deadline = time.monotonic() + self.timeout + 1
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.monotonic()))
            incoming = self._recv()
            if incoming.get("type") == "client-discovery-request":
                self._send({"type": "client-discovery-response",
                            "requestId": incoming.get("requestId"),
                            "response": {"canHandle": False}})
            elif incoming.get("type") == "request":
                self._send({"type": "response", "requestId": incoming.get("requestId"),
                            "resultType": "error", "error": "no-handler-for-request"})
            elif incoming.get("type") == "response" and incoming.get("requestId") == request_id:
                if incoming.get("resultType") != "success":
                    raise DesktopIpcError(f"Codex Desktop {method}: {incoming.get('error')}")
                result = incoming.get("result")
                return (result if isinstance(result, dict) else {}), incoming
        raise DesktopIpcError(f"Codex Desktop {method} timed out")

    def owner(self, thread_id: str) -> str:
        _, envelope = self._request("thread-owner-discovery", {
            "hostId": "local", "conversationId": thread_id}, 1)
        owner_id = str(envelope.get("handledByClientId") or "")
        if not owner_id:
            raise DesktopIpcError("Codex Desktop has not loaded the conversation")
        return owner_id

    def start_turn(self, thread_id: str, text: str, client_message_id: str,
                   cwd: str | None = None, *, permissions: str | None = None,
                   approval_policy: str | None = None,
                   sandbox_policy: dict | None = None,
                   model: str | None = None,
                   effort: str | None = None) -> str:
        owner_id = self.owner(thread_id)
        request = {"threadId": thread_id,
                   "input": [{"type": "text", "text": text, "text_elements": []}],
                   "clientUserMessageId": client_message_id}
        if cwd:
            request["cwd"] = cwd
        if permissions and sandbox_policy:
            raise DesktopIpcError("permissions and sandbox_policy cannot be combined")
        if permissions:
            request["permissions"] = permissions
        if sandbox_policy:
            request["sandboxPolicy"] = sandbox_policy
        if approval_policy:
            request["approvalPolicy"] = approval_policy
        if model:
            request["model"] = model
        if effort:
            request["effort"] = effort
        result, _ = self._request("thread-follower-start-turn", {
            "conversationId": thread_id,
            "turnStart": {
                "request": request,
                "context": {"attachments": [], "commentAttachments": [],
                            "inheritThreadSettings": False, "threadStartKind": "user"},
            },
        }, 2, target=owner_id)
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        turn_id = payload.get("turn", {}).get("id")
        if not isinstance(turn_id, str):
            raise DesktopIpcError("Codex Desktop did not return a turn ID")
        return turn_id
