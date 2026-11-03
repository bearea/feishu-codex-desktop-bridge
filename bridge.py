#!/usr/bin/env python3
"""Receive Feishu bot messages and reply through native Codex Desktop chats."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from desktop_ipc import DesktopIpc, DesktopIpcError


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("FEISHU_CODEX_CONFIG", str(ROOT / "config.json"))).expanduser()


def load_config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("config.json must contain a JSON object")
    return data


CONFIG = load_config()


def setting(name: str, default: str = "") -> str:
    value = os.environ.get("FEISHU_CODEX_" + name.upper(), CONFIG.get(name, default))
    return str(value or "")


_project_path = Path(setting("project_dir", "./feishu-message")).expanduser()
SESSION_CWD = (_project_path if _project_path.is_absolute() else ROOT / _project_path).resolve()
DATA_DIR = ROOT / ".state"
STATE = DATA_DIR / "replied_message_ids.json"
OUTBOX = DATA_DIR / "pending_audits.json"
THREADS = DATA_DIR / "live_threads.json"
PROFILE = setting("lark_profile", "futou_bot")
BOT_DISPLAY_NAME = setting("bot_display_name", "福头 ✨")
OWNER_OPEN_ID = setting("owner_open_id")
OWNER_DISPLAY_NAME = setting("owner_display_name")
MODEL = setting("model", "gpt-6.1-sol")
ROUTER_THREAD_ID = setting("router_thread_id")
CODEX_BUNDLE_ID = setting("codex_bundle_id", "com.openai.codex")
running = True
listener: subprocess.Popen[str] | None = None


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False
    if listener and listener.poll() is None:
        listener.terminate()


def load_done() -> set[str]:
    try:
        data = json.loads(STATE.read_text())
        return set(data) if isinstance(data, list) else set()
    except (FileNotFoundError, ValueError):
        return set()


def save_private_json(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(temporary, path)


def save_done(done: set[str]) -> None:
    save_private_json(STATE, sorted(done))


def load_outbox() -> dict[str, dict]:
    try:
        data = json.loads(OUTBOX.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_outbox(outbox: dict[str, dict]) -> None:
    save_private_json(OUTBOX, outbox)


def load_threads() -> dict[str, str]:
    try:
        data = json.loads(THREADS.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def rpc_result(process: subprocess.Popen[str], request_id: int, notifications: list[dict] | None = None) -> dict:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if not selector.select(1):
                continue
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Codex app-server closed unexpectedly")
            response = json.loads(line)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(f"Codex app-server request {request_id} failed: {response['error'].get('message')}")
                return response.get("result") or {}
            if notifications is not None and response.get("method"):
                notifications.append(response)
    finally:
        selector.close()
    raise RuntimeError("Codex app-server did not respond")


def send_rpc(process: subprocess.Popen[str], request_id: int, method: str, params: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
    process.stdin.flush()


def final_answer(turn: dict) -> str:
    for item in reversed(turn.get("items", [])):
        if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
            return str(item.get("text") or "").strip()
    return ""


def thread_key(record: dict) -> str:
    if record["chat_type"] == "p2p":
        return record["chat_id"]
    topic = record.get("root_id") or record.get("thread_id") or record["message_id"]
    return f"group:{record['chat_id']}:{topic}:{record['sender_id']}"


def thread_title(record: dict) -> str:
    sender = record.get("sender_name") or (
        OWNER_DISPLAY_NAME if OWNER_OPEN_ID and record["sender_id"] == OWNER_OPEN_ID
        and OWNER_DISPLAY_NAME else record["sender_id"][-10:])
    topic = record["question"].strip()
    if topic.startswith("/新会话 "):
        topic = topic[len("/新会话 "):].strip()
    elif topic.startswith("新会话："):
        topic = topic[len("新会话："):].strip()
    topic = topic.replace("\n", " ")[:22]
    if record["chat_type"] == "group":
        return f"{BOT_DISPLAY_NAME}｜群聊 {record['chat_id'][-8:]}｜{sender}｜{topic}"
    return f"{BOT_DISPLAY_NAME}｜{sender}｜{topic}"


def lookup_sender_name(record: dict) -> str | None:
    try:
        result = subprocess.run(
            ["lark-cli", "--profile", PROFILE, "contact", "+get-user",
             "--user-id", record["sender_id"], "--as", "bot", "--json"],
            text=True, capture_output=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        log("sender name unavailable: lookup timed out")
        return None
    if result.returncode != 0:
        try:
            error = json.loads(result.stderr).get("error") or {}
            log(f"sender name unavailable: {error.get('subtype') or error.get('type') or result.returncode}")
        except ValueError:
            log(f"sender name unavailable: CLI status {result.returncode}")
        return None
    try:
        payload = json.loads(result.stdout)
        if not payload.get("ok"):
            return None
        data = payload.get("data") or {}
        user = data.get("user") if isinstance(data.get("user"), dict) else data
        name = user.get("name") or user.get("localized_name") or user.get("en_name")
        if not isinstance(name, str):
            return None
        name = " ".join(name.replace("｜", " ").split())[:30]
        return name if name and name != record["sender_id"] else None
    except (ValueError, AttributeError):
        return None


@contextmanager
def app_server():
    """Use a separate process only for creation and read-only history queries."""
    process = subprocess.Popen(["codex", "app-server", "--listen", "stdio://"],
                               cwd=SESSION_CWD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        send_rpc(process, 1, "initialize", {"clientInfo": {
            "name": "feishu_codex_bridge", "title": BOT_DISPLAY_NAME, "version": "1.0.0"}})
        rpc_result(process, 1)
        assert process.stdin is not None
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def open_in_desktop(thread_id: str) -> None:
    result = subprocess.run(["/usr/bin/open", "-g", "-b", CODEX_BUNDLE_ID,
                             f"codex://threads/{thread_id}"], capture_output=True, text=True,
                            timeout=10, check=False)
    if result.returncode:
        raise RuntimeError(f"Could not open Codex Desktop: {result.stderr.strip()[:200]}")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and running:
        try:
            with DesktopIpc(timeout=3) as desktop:
                desktop.owner(thread_id)
                return
        except (DesktopIpcError, OSError, TimeoutError):
            time.sleep(0.5)
    raise RuntimeError(f"Codex Desktop did not load thread {thread_id}")


def read_history(thread_id: str) -> list[dict]:
    with app_server() as process:
        send_rpc(process, 2, "thread/read", {"threadId": thread_id, "includeTurns": True})
        return rpc_result(process, 2).get("thread", {}).get("turns", [])


def find_message_turn(turns: list[dict], message_id: str) -> dict | None:
    marker = f"飞书消息 ID：{message_id}\n"
    for turn in reversed(turns):
        for item in turn.get("items", []):
            if item.get("type") == "userMessage":
                if any(marker in str(part.get("text") or "") for part in item.get("content", [])):
                    return turn
    return None


def wait_for_completed(thread_id: str, turn_id: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while running and time.monotonic() < deadline:
        turns = read_history(thread_id)
        turn = next((item for item in turns if item.get("id") == turn_id), None)
        if turn and turn.get("status") == "completed":
            answer = final_answer(turn)
            if not answer:
                raise RuntimeError(f"Codex turn {turn_id} completed without an answer")
            return answer
        if turn and turn.get("status") == "failed":
            raise RuntimeError(f"Codex turn {turn_id} failed")
        time.sleep(0.8)
    raise RuntimeError(f"Codex turn {turn_id} did not complete in time")


def bind_project(thread_id: str, record: dict, outbox: dict[str, dict]) -> None:
    if record.get("project_bound"):
        return
    if not ROUTER_THREAD_ID:
        raise RuntimeError("FUTOU_ROUTER_THREAD_ID is required for new chats")
    bind_turn_id = record.get("bind_turn_id")
    if not bind_turn_id:
        open_in_desktop(ROUTER_THREAD_ID)
        request = json.dumps({"operation": "bind_project", "thread_id": thread_id},
                             ensure_ascii=False)
        with DesktopIpc() as desktop:
            bind_turn_id = desktop.start_turn(ROUTER_THREAD_ID, request,
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"futou-bind:{thread_id}")))
        record["bind_turn_id"] = bind_turn_id
        save_outbox(outbox)
    answer = wait_for_completed(ROUTER_THREAD_ID, bind_turn_id, 90)
    try:
        result = json.loads(answer)
    except ValueError as exc:
        raise RuntimeError(f"Project binding returned invalid JSON: {answer[:150]}") from exc
    if (result.get("status") != "completed" or result.get("operation") != "bind_project"
            or result.get("thread_id") != thread_id):
        raise RuntimeError(f"Project binding failed: {answer[:150]}")
    record["project_bound"] = True
    save_outbox(outbox)
    log(f"bound Codex thread {thread_id} to {SESSION_CWD.name}")


def ask_codex(message_id: str, record: dict, threads: dict[str, str], outbox: dict[str, dict]) -> str:
    key = thread_key(record)
    raw_time = str(record["create_time"])
    try:
        display_time = datetime.fromtimestamp(int(raw_time) / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        display_time = raw_time
    thread_id = record.get("codex_thread_id") or threads.get(key)
    if not thread_id:
        if not record.get("sender_name"):
            record["sender_name"] = lookup_sender_name(record)
            save_outbox(outbox)
        with app_server() as process:
            send_rpc(process, 2, "thread/start", {
                "model": MODEL, "cwd": str(SESSION_CWD), "approvalPolicy": "never",
                "sandbox": "read-only", "threadSource": "cli", "serviceName": "feishu_codex_bridge"})
            thread_id = rpc_result(process, 2).get("thread", {}).get("id")
            if not isinstance(thread_id, str):
                raise RuntimeError("Codex did not create a thread")
            send_rpc(process, 3, "thread/name/set", {"threadId": thread_id,
                                                      "name": thread_title(record)})
            rpc_result(process, 3)
        record["codex_thread_id"] = thread_id
        record["needs_binding"] = True
        save_outbox(outbox)
        threads[key] = thread_id
        save_private_json(THREADS, threads)
        log(f"created empty Codex thread {thread_id} for Feishu key {key}")
        open_in_desktop(thread_id)
        bind_project(thread_id, record, outbox)
    else:
        record["codex_thread_id"] = thread_id
        save_outbox(outbox)
        if record.get("needs_binding") and not record.get("project_bound"):
            open_in_desktop(thread_id)
            bind_project(thread_id, record, outbox)

    turn_id = record.get("codex_turn_id")
    if not turn_id:
        prior = find_message_turn(read_history(thread_id), message_id)
        if prior:
            turn_id = prior["id"]
            record["codex_turn_id"] = turn_id
            save_outbox(outbox)
    if not turn_id:
        sender = record.get("sender_name") or record["sender_id"]
        prompt = (
            f"飞书提问人：{sender}（{record['sender_id']}）\n飞书会话：{record['chat_id']}\n"
            f"飞书提问时间：{display_time}\n飞书消息 ID：{message_id}\n\n{record['question']}"
        )
        open_in_desktop(thread_id)
        record["start_intent"] = True
        save_outbox(outbox)
        with DesktopIpc() as desktop:
            turn_id = desktop.start_turn(thread_id, prompt,
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"futou-message:{message_id}")),
                cwd=str(SESSION_CWD))
        record["codex_turn_id"] = turn_id
        save_outbox(outbox)
        log(f"Codex Desktop turn started for Feishu message {message_id}: {turn_id}")
    answer = wait_for_completed(thread_id, turn_id, 360)
    if len(answer) > 9000:
        raise RuntimeError("Codex answer exceeds the Feishu message limit")
    return answer


def send_text(message_id: str, purpose: str, target_type: str, target: str, text: str) -> None:
    key = "feishu-codex-" + purpose + "-" + hashlib.sha256(message_id.encode()).hexdigest()[:32]
    result = subprocess.run(
        [
            "lark-cli", "--profile", PROFILE, "im", "+messages-send",
            "--as", "bot", target_type, target, "--text", text,
            "--idempotency-key", key, "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Feishu send failed with status {result.returncode}: {result.stdout[-500:]}")
    payload = json.loads(result.stdout)
    if payload.get("ok") is not True:
        raise RuntimeError(f"Feishu send rejected: {payload.get('error', {}).get('type', 'unknown')}")


def flush_outbox(outbox: dict[str, dict], done: set[str], threads: dict[str, str]) -> None:
    for message_id, record in list(outbox.items()):
        if not record.get("answer"):
            record["answer"] = ask_codex(message_id, record, threads, outbox)
            save_outbox(outbox)
        if not record["sent"]:
            send_text(message_id, "reply", "--chat-id", record["chat_id"], record["answer"])
            record["sent"] = True
            save_outbox(outbox)
        done.add(message_id)
        save_done(done)
        del outbox[message_id]
        save_outbox(outbox)
        log(f"answered in Codex and Feishu for message {message_id}")


def handle(event: dict, done: set[str], outbox: dict[str, dict], threads: dict[str, str]) -> None:
    if event.get("type") != "im.message.receive_v1":
        return
    message_id = event.get("message_id") or event.get("id")
    if not isinstance(message_id, str) or not message_id.startswith("om_") or message_id in done:
        return
    if message_id in outbox:
        flush_outbox(outbox, done, threads)
        return
    if event.get("sender_type") != "user":
        return
    if event.get("chat_type") not in ("p2p", "group") or event.get("message_type") != "text":
        log("ignored non-text message")
        return
    chat_id = event.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id.startswith("oc_"):
        log("ignored message without a valid chat ID")
        return
    question = str(event.get("content") or "").strip()
    if not question:
        return
    original_question = question
    key = thread_key({"chat_type": event["chat_type"], "chat_id": chat_id,
                      "root_id": event.get("root_id"), "thread_id": event.get("thread_id"),
                      "message_id": message_id, "sender_id": str(event.get("sender_id") or "unknown")})
    if question.startswith("/新会话 ") or question.startswith("新会话："):
        question = question.split(" ", 1)[1].strip() if question.startswith("/新会话 ") else question[4:].strip()
        threads.pop(key, None)
        save_private_json(THREADS, threads)
    if not question:
        return
    log(f"processing message {message_id}")
    outbox[message_id] = {
        "chat_id": chat_id,
        "sender_id": str(event.get("sender_id") or "unknown"),
        "chat_type": event["chat_type"],
        "message_id": message_id,
        "root_id": event.get("root_id"),
        "thread_id": event.get("thread_id"),
        "question": original_question,
        "answer": None,
        "create_time": str(event.get("create_time") or "unknown"),
        "sent": False,
    }
    save_outbox(outbox)
    flush_outbox(outbox, done, threads)


def main() -> None:
    global listener
    if sys.platform != "darwin":
        raise RuntimeError("This bridge currently supports macOS Codex Desktop only")
    if not SESSION_CWD.is_dir() or not (SESSION_CWD / "AGENTS.md").is_file():
        raise RuntimeError(f"Codex project and AGENTS.md are required: {SESSION_CWD}")
    if not ROUTER_THREAD_ID:
        raise RuntimeError("router_thread_id is required in config.json")
    for name in ("codex", "lark-cli"):
        if not shutil.which(name):
            raise RuntimeError(f"{name} is not available on PATH")
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    done = load_done()
    outbox = load_outbox()
    threads = load_threads()
    while running:
        try:
            flush_outbox(outbox, done, threads)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            log(f"answer delivery pending: {exc}")
            time.sleep(15)
            continue
        listener = subprocess.Popen(
            [
                "lark-cli", "--profile", PROFILE, "event", "consume",
                "im.message.receive_v1", "--as", "bot", "--timeout", "5m",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        ready = threading.Event()

        def forward_event_diagnostics(process: subprocess.Popen[str]) -> None:
            assert process.stderr is not None
            for diagnostic in process.stderr:
                message = diagnostic.rstrip()
                if message.startswith("[event] ready event_key=im.message.receive_v1"):
                    ready.set()
                log(f"lark-cli: {message}")

        threading.Thread(target=forward_event_diagnostics, args=(listener,), daemon=True).start()
        if not ready.wait(20):
            log("Feishu event listener did not become ready")
            listener.terminate()
            listener.wait(timeout=5)
            time.sleep(3)
            continue
        log("Feishu event listener started")
        assert listener.stdout is not None
        for line in listener.stdout:
            if not running:
                break
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    handle(event, done, outbox, threads)
            except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                log(f"event handling failed: {exc}")
                if outbox:
                    listener.terminate()
                    break
        if listener.poll() is None:
            listener.terminate()
        try:
            listener.wait(timeout=5)
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait()
        if running:
            log("Feishu listener disconnected; retrying")
            time.sleep(3)


if __name__ == "__main__":
    main()
