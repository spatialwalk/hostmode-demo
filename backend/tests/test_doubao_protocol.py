from app.doubao.protocol import (
    BinaryProtocol,
    DoubaoMessage,
    MessageFlag,
    MessageType,
    SERIALIZATION_JSON,
    SERIALIZATION_RAW,
)


def test_full_client_roundtrip() -> None:
    protocol = BinaryProtocol()
    protocol.set_serialization(SERIALIZATION_JSON)

    message = DoubaoMessage.create(MessageType.FULL_CLIENT, MessageFlag.WITH_EVENT)
    message.event = 100
    message.session_id = "session-123"
    message.payload = b'{"hello":"world"}'

    encoded = protocol.marshal(message)
    decoded, decoded_protocol = BinaryProtocol.unmarshal(encoded)

    assert decoded_protocol.serialization == SERIALIZATION_JSON
    assert decoded.type == MessageType.FULL_CLIENT
    assert decoded.event == 100
    assert decoded.session_id == "session-123"
    assert decoded.payload == b'{"hello":"world"}'


def test_audio_only_server_roundtrip() -> None:
    protocol = BinaryProtocol()
    protocol.set_serialization(SERIALIZATION_RAW)

    message = DoubaoMessage.create(
        MessageType.AUDIO_ONLY_SERVER,
        MessageFlag.WITH_EVENT,
    )
    message.event = 352
    message.session_id = "session-123"
    message.payload = b"\x01\x02\x03"

    encoded = protocol.marshal(message)
    decoded, decoded_protocol = BinaryProtocol.unmarshal(encoded)

    assert decoded_protocol.serialization == SERIALIZATION_RAW
    assert decoded.type == MessageType.AUDIO_ONLY_SERVER
    assert decoded.event == 352
    assert decoded.session_id == "session-123"
    assert decoded.payload == b"\x01\x02\x03"


def test_connection_ack_reads_connect_id_without_session_id() -> None:
    connect_id = b"connect-123"
    payload = b"{}"
    frame = b"".join(
        [
            bytes([0x11, 0x94, 0x10, 0x00]),
            (50).to_bytes(4, "big", signed=True),
            len(connect_id).to_bytes(4, "big"),
            connect_id,
            len(payload).to_bytes(4, "big"),
            payload,
        ]
    )

    decoded, decoded_protocol = BinaryProtocol.unmarshal(frame)

    assert decoded_protocol.serialization == SERIALIZATION_JSON
    assert decoded.type == MessageType.FULL_SERVER
    assert decoded.event == 50
    assert decoded.session_id == ""
    assert decoded.connect_id == "connect-123"
    assert decoded.payload == b"{}"
