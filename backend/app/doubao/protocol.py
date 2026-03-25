from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


class MessageType(IntEnum):
    INVALID = 0
    FULL_CLIENT = 1
    AUDIO_ONLY_CLIENT = 2
    FULL_SERVER = 3
    AUDIO_ONLY_SERVER = 4
    FRONT_END_RESULT_SERVER = 5
    ERROR = 6


class MessageFlag(IntEnum):
    NO_SEQUENCE = 0b0000
    POSITIVE_SEQUENCE = 0b0001
    LAST_NO_SEQUENCE = 0b0010
    NEGATIVE_SEQUENCE = 0b0011
    WITH_EVENT = 0b0100


VERSION_1 = 0x10
HEADER_SIZE_4 = 0x01
SERIALIZATION_RAW = 0x00
SERIALIZATION_JSON = 0x10
COMPRESSION_NONE = 0x00

MESSAGE_TYPE_TO_BITS: dict[MessageType, int] = {
    MessageType.FULL_CLIENT: 0x10,
    MessageType.AUDIO_ONLY_CLIENT: 0x20,
    MessageType.FULL_SERVER: 0x90,
    MessageType.AUDIO_ONLY_SERVER: 0xB0,
    MessageType.FRONT_END_RESULT_SERVER: 0xC0,
    MessageType.ERROR: 0xF0,
}
BITS_TO_MESSAGE_TYPE = {bits: kind for kind, bits in MESSAGE_TYPE_TO_BITS.items()}


def contains_sequence(flag_bits: int) -> bool:
    return (
        flag_bits & MessageFlag.POSITIVE_SEQUENCE == MessageFlag.POSITIVE_SEQUENCE
        or flag_bits & MessageFlag.NEGATIVE_SEQUENCE == MessageFlag.NEGATIVE_SEQUENCE
    )


def contains_event(flag_bits: int) -> bool:
    return flag_bits & MessageFlag.WITH_EVENT == MessageFlag.WITH_EVENT


@dataclass(slots=True)
class DoubaoMessage:
    type: MessageType
    flag_bits: int
    event: int = 0
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""

    @classmethod
    def create(cls, msg_type: MessageType, flag_bits: int) -> "DoubaoMessage":
        if msg_type not in MESSAGE_TYPE_TO_BITS:
            raise ValueError(f"unsupported message type: {msg_type}")
        return cls(type=msg_type, flag_bits=flag_bits)

    @property
    def type_and_flag_byte(self) -> int:
        return MESSAGE_TYPE_TO_BITS[self.type] | self.flag_bits


class BinaryProtocol:
    def __init__(self) -> None:
        self.version_and_header_size = VERSION_1 | HEADER_SIZE_4
        self.serialization_and_compression = SERIALIZATION_JSON | COMPRESSION_NONE

    @property
    def header_size(self) -> int:
        return 4 * (self.version_and_header_size & 0x0F)

    @property
    def serialization(self) -> int:
        return self.serialization_and_compression & 0xF0

    def set_serialization(self, serialization: int) -> None:
        self.serialization_and_compression = (
            serialization | (self.serialization_and_compression & 0x0F)
        )

    def marshal(self, message: DoubaoMessage) -> bytes:
        header = bytes(
            [
                self.version_and_header_size,
                message.type_and_flag_byte,
                self.serialization_and_compression,
                0x00,
            ]
        )
        chunks: list[bytes] = [header]

        if contains_sequence(message.flag_bits):
            chunks.append(struct.pack(">i", message.sequence))

        if contains_event(message.flag_bits):
            chunks.append(struct.pack(">i", message.event))
            if message.event not in {1, 2, 50, 51, 52}:
                session_id = message.session_id.encode("utf-8")
                chunks.append(struct.pack(">I", len(session_id)))
                chunks.append(session_id)

        payload = message.payload or b""
        chunks.append(struct.pack(">I", len(payload)))
        chunks.append(payload)
        return b"".join(chunks)

    @classmethod
    def unmarshal(cls, data: bytes) -> tuple[DoubaoMessage, "BinaryProtocol"]:
        if len(data) < 4:
            raise ValueError("invalid doubao frame: missing header")

        version_and_size = data[0]
        type_and_flag = data[1]
        serialization_and_compression = data[2]
        header_size = 4 * (version_and_size & 0x0F)
        if len(data) < header_size:
            raise ValueError("invalid doubao frame: truncated header")

        msg_bits = type_and_flag & 0xF0
        msg_type = BITS_TO_MESSAGE_TYPE.get(msg_bits)
        if msg_type is None:
            raise ValueError(f"invalid doubao message type: {msg_bits >> 4:b}")

        message = DoubaoMessage(
            type=msg_type,
            flag_bits=type_and_flag & 0x0F,
        )
        protocol = cls()
        protocol.version_and_header_size = version_and_size
        protocol.serialization_and_compression = serialization_and_compression

        cursor = header_size

        if msg_type == MessageType.AUDIO_ONLY_CLIENT and contains_sequence(
            message.flag_bits
        ):
            message.sequence, cursor = _read_i32(data, cursor)
        elif msg_type == MessageType.AUDIO_ONLY_SERVER and contains_sequence(
            message.flag_bits
        ):
            message.sequence, cursor = _read_i32(data, cursor)
        elif msg_type == MessageType.ERROR:
            message.error_code, cursor = _read_u32(data, cursor)

        if contains_event(message.flag_bits):
            message.event, cursor = _read_i32(data, cursor)
            if message.event not in {1, 2, 50, 51, 52}:
                size, cursor = _read_u32(data, cursor)
                if size:
                    message.session_id = data[cursor : cursor + size].decode("utf-8")
                    cursor += size
            if message.event in {50, 51, 52}:
                connect_size, cursor = _read_u32(data, cursor)
                if connect_size:
                    message.connect_id = data[
                        cursor : cursor + connect_size
                    ].decode("utf-8")
                    cursor += connect_size

        payload_size, cursor = _read_u32(data, cursor)
        message.payload = data[cursor : cursor + payload_size]
        cursor += payload_size
        if cursor != len(data):
            raise ValueError("invalid doubao frame: redundant bytes")
        return message, protocol


def _read_i32(data: bytes, cursor: int) -> tuple[int, int]:
    if cursor + 4 > len(data):
        raise ValueError("invalid doubao frame: missing i32")
    return struct.unpack(">i", data[cursor : cursor + 4])[0], cursor + 4


def _read_u32(data: bytes, cursor: int) -> tuple[int, int]:
    if cursor + 4 > len(data):
        raise ValueError("invalid doubao frame: missing u32")
    return struct.unpack(">I", data[cursor : cursor + 4])[0], cursor + 4
