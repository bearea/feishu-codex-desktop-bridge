"""Exercise the Desktop IPC wire shape with a local fake owner."""

import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_ipc


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        data.extend(sock.recv(size - len(data)))
    return bytes(data)


def receive(sock):
    size = struct.unpack("<I", read_exact(sock, 4))[0]
    data = read_exact(sock, size)
    return json.loads(data)


def send(sock, value):
    data = json.dumps(value).encode()
    sock.sendall(struct.pack("<I", len(data)) + data)


class DesktopIpcTests(unittest.TestCase):
    def test_owner_submits_a_real_user_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "desktop.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(1)
            received = []

            def serve():
                conn, _ = server.accept()
                with conn:
                    for _ in range(3):
                        request = receive(conn)
                        received.append(request)
                        result = {"type": "response", "requestId": request["requestId"],
                                  "resultType": "success"}
                        if request["method"] == "initialize":
                            result["result"] = {"clientId": "bridge-client"}
                        elif request["method"] == "thread-owner-discovery":
                            result["handledByClientId"] = "desktop-owner"
                        else:
                            result["result"] = {"result": {"turn": {"id": "turn-1"}}}
                        send(conn, result)

            thread = threading.Thread(target=serve)
            thread.start()
            try:
                with patch.object(desktop_ipc, "SOCKET_PATH", path):
                    with desktop_ipc.DesktopIpc(timeout=2) as client:
                        turn_id = client.start_turn("thread-1", "原始问题", "message-1",
                            permissions="futou_nova_read", approval_policy="never",
                            model="gpt-6.1-sol", effort="high")
                self.assertEqual(turn_id, "turn-1")
                self.assertEqual([r["method"] for r in received],
                                 ["initialize", "thread-owner-discovery", "thread-follower-start-turn"])
                submitted = received[-1]
                self.assertEqual(submitted["targetClientId"], "desktop-owner")
                self.assertEqual(submitted["params"]["turnStart"]["request"]["input"][0]["text"],
                                 "原始问题")
                request = submitted["params"]["turnStart"]["request"]
                self.assertEqual(request["permissions"], "futou_nova_read")
                self.assertEqual(request["approvalPolicy"], "never")
                self.assertEqual(request["model"], "gpt-6.1-sol")
                self.assertEqual(request["effort"], "high")
                self.assertFalse(submitted["params"]["turnStart"]["context"]["inheritThreadSettings"])
            finally:
                thread.join(timeout=3)
                server.close()


if __name__ == "__main__":
    unittest.main()
