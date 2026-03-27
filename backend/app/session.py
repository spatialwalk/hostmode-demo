from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from app.avatar.turn import AvatarTurn, AvatarTurnEvent
from app.config import Settings
from app.doubao.client import DoubaoRealtimeClient, DoubaoResponse


logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(self, websocket: WebSocket, settings: Settings) -> None:
        self._websocket = websocket
        self._settings = settings
        self._send_lock = asyncio.Lock()
        self._doubao = DoubaoRealtimeClient(settings)
        self._doubao_task: asyncio.Task[None] | None = None
        self._avatar_turn: AvatarTurn | None = None
        self._avatar_turn_task: asyncio.Task[None] | None = None
        self._active_turn_id: str | None = None
        self._client_avatar_id: str | None = None

    async def run(self) -> None:
        await self._websocket.accept()
        await self._doubao.connect()
        self._doubao_task = asyncio.create_task(self._consume_doubao())
        await self._send(
            {
                "type": "ready",
                "sessionId": str(uuid4()),
                "avatar": self._settings.public_avatar_config,
            }
        )

        try:
            while True:
                payload = await self._websocket.receive_json()
                await self._handle_client_message(payload)
        except WebSocketDisconnect:
            logger.info("browser websocket disconnected")
        finally:
            await self.close()

    async def close(self) -> None:
        tasks = [self._doubao_task, self._avatar_turn_task]
        for task in tasks:
            if task is not None:
                task.cancel()
        self._doubao_task = None
        self._avatar_turn_task = None
        await self._close_avatar_turn()
        await self._doubao.close()

    async def _handle_client_message(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        logger.info("Client message: %s", kind)
        if kind == "ping":
            await self._send({"type": "pong"})
            return
        if kind == "set_avatar":
            # Allow clients to dynamically override the avatar ID from .env.
            # If not sent, _start_avatar_turn falls back to settings.avatar_id.
            avatar_id = str(payload.get("avatarId", "")).strip()
            if avatar_id:
                self._client_avatar_id = avatar_id
                await self._send_status(f"Avatar ID set to: {avatar_id}")
            return
        if kind == "text_query":
            text = str(payload.get("text", "")).strip()
            if not text:
                return
            await self._doubao.send_text_query(text)
            await self._send_status(f"Sent text prompt: {text}")
            return
        if kind == "mic_audio":
            audio_b64 = str(payload.get("audio", ""))
            if not audio_b64:
                return
            await self._doubao.send_audio(base64.b64decode(audio_b64))
            return
        if kind == "interrupt":
            await self._interrupt_current_turn(reason="client_interrupt")
            return
        if kind == "mic_end":
            await self._send_status("Microphone stream paused")
            return

        await self._send(
            {
                "type": "error",
                "message": f"Unsupported client message: {kind}",
            }
        )

    async def _consume_doubao(self) -> None:
        queue = self._doubao.responses
        while True:
            response = await queue.get()
            if response.error:
                if response.error == "closed":
                    return
                await self._send({"type": "error", "message": response.error})
                continue
            await self._handle_doubao_event(response)

    async def _handle_doubao_event(self, response: DoubaoResponse) -> None:
        event = response.event
        if event == 350:
            await self._start_avatar_turn()
            await self._send(
                {
                    "type": "agent_event",
                    "event": "tts_start",
                    "turnId": self._active_turn_id,
                }
            )
            return
        if event == 352 and response.audio:
            if self._avatar_turn is None:
                await self._start_avatar_turn()
            assert self._active_turn_id is not None
            await self._send(
                {
                    "type": "avatar_audio",
                    "turnId": self._active_turn_id,
                    "audio": base64.b64encode(response.audio).decode("ascii"),
                    "isLast": False,
                }
            )
            if self._avatar_turn is not None:
                await self._avatar_turn.send_audio(response.audio, end=False)
            return
        if event == 351:
            if self._active_turn_id is not None:
                await self._send(
                    {
                        "type": "avatar_audio",
                        "turnId": self._active_turn_id,
                        "audio": "",
                        "isLast": True,
                    }
                )
            if self._avatar_turn is not None:
                await self._avatar_turn.send_audio(b"", end=True)
            await self._send(
                {
                    "type": "agent_event",
                    "event": "tts_end",
                    "turnId": self._active_turn_id,
                }
            )
            return
        if event == 450:
            await self._interrupt_current_turn(reason="barge_in")
            data = _parse_payload(response.payload)
            text = str(data.get("text", "")).strip()
            if text:
                await self._send(
                    {
                        "type": "agent_event",
                        "event": "asr_interim",
                        "text": text,
                    }
                )
            return
        if event == 451:
            data = _parse_payload(response.payload)
            results = data.get("results") or []
            if results and isinstance(results[0], dict):
                result = results[0]
                text = str(result.get("text", "")).strip()
                if text:
                    await self._send(
                        {
                            "type": "agent_event",
                            "event": "asr_final",
                            "text": text,
                            "isSoftFinished": bool(
                                result.get("is_soft_finished", False)
                            ),
                        }
                    )
            return
        if event == 550:
            data = _parse_payload(response.payload)
            content = str(data.get("content", "")).strip()
            if content:
                await self._send(
                    {
                        "type": "agent_event",
                        "event": "llm_text",
                        "text": content,
                    }
                )
            return
        if response.payload:
            await self._send(
                {
                    "type": "agent_event",
                    "event": f"event_{event}",
                    "payload": _parse_payload(response.payload),
                }
            )

    async def _start_avatar_turn(self) -> None:
        await self._close_avatar_turn()
        turn_id = str(uuid4())
        turn = AvatarTurn(self._settings, turn_id=turn_id, avatar_id=self._client_avatar_id)
        await turn.start()
        self._avatar_turn = turn
        self._active_turn_id = turn_id
        self._avatar_turn_task = asyncio.create_task(self._forward_avatar_frames(turn))

    async def _close_avatar_turn(self) -> None:
        turn_task = self._avatar_turn_task
        self._avatar_turn_task = None
        if turn_task is not None:
            turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn_task
        turn = self._avatar_turn
        self._avatar_turn = None
        self._active_turn_id = None
        if turn is not None:
            await turn.close()

    async def _forward_avatar_frames(self, turn: AvatarTurn) -> None:
        try:
            while True:
                event = await turn.queue.get()
                await self._handle_avatar_event(turn.turn_id, event)
                if event.kind == "frame" and event.is_last:
                    await turn.close()
                    if self._avatar_turn is turn:
                        self._avatar_turn = None
                        self._active_turn_id = None
                    return
                if event.kind in {"error", "closed"}:
                    return
        except asyncio.CancelledError:
            raise

    async def _handle_avatar_event(
        self,
        turn_id: str,
        event: AvatarTurnEvent,
    ) -> None:
        if turn_id != self._active_turn_id:
            return
        if event.kind == "frame":
            await self._send(
                {
                    "type": "avatar_frames",
                    "turnId": turn_id,
                    "frames": [base64.b64encode(event.frame).decode("ascii")],
                    "isLast": event.is_last,
                }
            )
            return
        if event.kind == "error":
            await self._send(
                {
                    "type": "error",
                    "message": f"Avatar turn failed: {event.message}",
                }
            )

    async def _interrupt_current_turn(self, *, reason: str) -> None:
        await self._send({"type": "interrupt", "reason": reason})
        await self._close_avatar_turn()

    async def _send_status(self, message: str) -> None:
        await self._send({"type": "status", "message": message})

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._websocket.send_text(json.dumps(payload, ensure_ascii=False))


def _parse_payload(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": payload.decode("utf-8", errors="replace")}
