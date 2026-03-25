from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
import logging
import time
import uuid

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.config import Settings
from app.doubao.protocol import (
    BinaryProtocol,
    DoubaoMessage,
    MessageFlag,
    MessageType,
    SERIALIZATION_JSON,
    SERIALIZATION_RAW,
)


logger = logging.getLogger(__name__)

_SILENCE_INTERVAL_MS = 100
_SILENCE_FRAME = b"\x00" * 320


@dataclass(slots=True)
class DoubaoResponse:
    event: int
    audio: bytes = b""
    payload: bytes = b""
    error: str | None = None


class DoubaoRealtimeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._protocol = BinaryProtocol()
        self._ws: ClientConnection | None = None
        self._session_id = ""
        self._dialog_id = ""
        self._connect_id = str(uuid.uuid4())
        self._queue: asyncio.Queue[DoubaoResponse] = asyncio.Queue(maxsize=256)
        self._receive_task: asyncio.Task[None] | None = None
        self._silence_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._last_audio_at = time.monotonic()

    @property
    def responses(self) -> asyncio.Queue[DoubaoResponse]:
        return self._queue

    async def connect(self) -> None:
        if self._ws is not None:
            return

        self._session_id = str(uuid.uuid4())
        url = f"wss://{self._settings.doubao_ws_host}{self._settings.doubao_ws_path}"
        headers = {
            "X-Api-Resource-Id": self._settings.doubao_resource_id,
            "X-Api-Access-Key": self._settings.doubao_access_token,
            "X-Api-App-Key": self._settings.doubao_app_key,
            "X-Api-App-ID": self._settings.doubao_app_id,
            "X-Api-Connect-Id": self._connect_id,
        }
        self._ws = await connect(url, additional_headers=headers, open_timeout=15)

        await self._start_connection()
        await self._start_session()

        self._receive_task = asyncio.create_task(self._receive_loop())
        self._silence_task = asyncio.create_task(self._silence_loop())
        logger.info("Doubao realtime session established")

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is None:
            return

        try:
            await self._send_finish_session(ws)
            await self._send_finish_connection(ws)
        except Exception:
            logger.debug("ignoring close-frame error", exc_info=True)

        for task in (self._receive_task, self._silence_task):
            if task is not None:
                task.cancel()
        self._receive_task = None
        self._silence_task = None

        await ws.close()
        await self._queue.put(DoubaoResponse(event=0, error="closed"))

    async def send_audio(self, audio: bytes) -> None:
        ws = self._require_ws()
        self._last_audio_at = time.monotonic()
        self._protocol.set_serialization(SERIALIZATION_RAW)
        message = DoubaoMessage.create(
            MessageType.AUDIO_ONLY_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 200
        message.session_id = self._session_id
        message.payload = audio
        await self._send_frame(ws, self._protocol.marshal(message))

    async def send_text_query(self, text: str) -> None:
        ws = self._require_ws()
        self._protocol.set_serialization(SERIALIZATION_JSON)
        message = DoubaoMessage.create(
            MessageType.FULL_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 501
        message.session_id = self._session_id
        message.payload = json.dumps({"content": text}).encode("utf-8")
        await self._send_frame(ws, self._protocol.marshal(message))

    def _require_ws(self) -> ClientConnection:
        if self._ws is None:
            raise RuntimeError("doubao websocket is not connected")
        return self._ws

    async def _send_frame(self, ws: ClientConnection, frame: bytes) -> None:
        async with self._write_lock:
            await ws.send(frame)

    async def _start_connection(self) -> None:
        ws = self._require_ws()
        message = DoubaoMessage.create(
            MessageType.FULL_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 1
        message.payload = b"{}"
        await self._send_frame(ws, self._protocol.marshal(message))
        response = await self._receive_once(ws)
        if response.event != 50:
            raise RuntimeError(f"unexpected doubao connection ack event: {response.event}")

    async def _start_session(self) -> None:
        ws = self._require_ws()
        self._protocol.set_serialization(SERIALIZATION_JSON)
        dialog_extra: dict[str, object] = {"input_mod": "audio"}
        if self._settings.doubao_model:
            dialog_extra["model"] = self._settings.doubao_model
        if self._settings.doubao_enable_music:
            dialog_extra["enable_music"] = True
        if self._settings.doubao_enable_web_search:
            dialog_extra["enable_volc_websearch"] = True

        payload = {
            "asr": {
                "extra": {
                    "end_smooth_window_ms": (
                        self._settings.doubao_end_smooth_window_ms
                    )
                }
            },
            "tts": {
                "speaker": self._settings.doubao_speaker,
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": self._settings.avatar_output_sample_rate,
                },
            },
            "dialog": {
                "bot_name": self._settings.doubao_bot_name,
                "system_role": self._settings.doubao_system_role,
                "speaking_style": self._settings.doubao_speaking_style,
                "extra": dialog_extra,
            },
        }

        message = DoubaoMessage.create(
            MessageType.FULL_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 100
        message.session_id = self._session_id
        message.payload = json.dumps(payload).encode("utf-8")
        await self._send_frame(ws, self._protocol.marshal(message))
        response = await self._receive_once(ws)
        if response.event != 150:
            raise RuntimeError(f"unexpected doubao session ack event: {response.event}")
        try:
            data = json.loads(response.payload.decode("utf-8"))
        except json.JSONDecodeError:
            data = {}
        self._dialog_id = data.get("dialog_id", "")

    async def _send_finish_session(self, ws: ClientConnection) -> None:
        message = DoubaoMessage.create(
            MessageType.FULL_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 102
        message.session_id = self._session_id
        message.payload = b"{}"
        await self._send_frame(ws, self._protocol.marshal(message))

    async def _send_finish_connection(self, ws: ClientConnection) -> None:
        message = DoubaoMessage.create(
            MessageType.FULL_CLIENT,
            MessageFlag.WITH_EVENT,
        )
        message.event = 2
        message.payload = b"{}"
        await self._send_frame(ws, self._protocol.marshal(message))

    async def _receive_once(self, ws: ClientConnection) -> DoubaoResponse:
        raw = await ws.recv()
        frame = raw.encode("utf-8") if isinstance(raw, str) else raw
        message, _ = BinaryProtocol.unmarshal(frame)
        return self._message_to_response(message)

    async def _receive_loop(self) -> None:
        ws = self._require_ws()
        try:
            async for raw in ws:
                frame = raw.encode("utf-8") if isinstance(raw, str) else raw
                message, _ = BinaryProtocol.unmarshal(frame)
                await self._queue.put(self._message_to_response(message))
        except ConnectionClosed:
            logger.info("Doubao websocket closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Doubao receive loop failed")
            await self._queue.put(DoubaoResponse(event=0, error=str(exc)))

    async def _silence_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_SILENCE_INTERVAL_MS / 1000)
                if self._ws is None:
                    return
                idle_ms = (time.monotonic() - self._last_audio_at) * 1000
                if idle_ms >= _SILENCE_INTERVAL_MS:
                    await self.send_audio(_SILENCE_FRAME)
        except asyncio.CancelledError:
            raise

    def _message_to_response(self, message: DoubaoMessage) -> DoubaoResponse:
        if message.type == MessageType.AUDIO_ONLY_SERVER:
            return DoubaoResponse(event=message.event, audio=message.payload)
        if message.type == MessageType.ERROR:
            return DoubaoResponse(
                event=message.event,
                error=f"doubao server error: {message.error_code}",
            )
        return DoubaoResponse(event=message.event, payload=message.payload)
