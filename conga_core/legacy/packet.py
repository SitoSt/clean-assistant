"""Trama (RSMessage) del protocolo legacy 3irobotix: cabecera de 24 bytes + payload.

Formato en el cable (little-endian), prefijado por su tamaño total:
    size(u32) ctype(u8) flow(u8) deviceId(u32) userId(u32) sequence(u64) opcode(u16)
    + payload (Protobuf, size-24 bytes)
`size` incluye los 24 bytes de cabecera (y a sí mismo como prefijo de longitud).

Construcción de respuestas copiada de @agnoc/core (connection.emitter.ts):
  respond:  ctype=req.ctype, flow=req.flow+1, userId/deviceId INTERCAMBIADOS,
            sequence=req.sequence
  send:     ctype=2, flow=0, sequence aleatorio, ids intercambiados
"""
from __future__ import annotations
import random
import struct

from .opcodes import OPCODES, name_of

HEADER = 24
_HDR = struct.Struct("<IBBIIQH")   # size, ctype, flow, deviceId, userId, sequence, opcode
assert _HDR.size == HEADER


def _code(opname) -> int:
    return OPCODES[opname] if isinstance(opname, str) else int(opname)


class Packet:
    __slots__ = ("opcode", "payload", "ctype", "flow", "device_id", "user_id", "sequence")

    def __init__(self, opcode, payload: bytes = b"", ctype: int = 2, flow: int = 0,
                 device_id: int = 0, user_id: int = 0, sequence: int = 0):
        self.opcode = _code(opcode)
        self.payload = bytes(payload)
        self.ctype = ctype
        self.flow = flow
        self.device_id = device_id
        self.user_id = user_id
        self.sequence = sequence

    @property
    def opname(self) -> str:
        return name_of(self.opcode)

    def to_bytes(self) -> bytes:
        size = HEADER + len(self.payload)
        return _HDR.pack(size, self.ctype, self.flow, self.device_id,
                         self.user_id, self.sequence, self.opcode) + self.payload

    @classmethod
    def from_bytes(cls, buf: bytes) -> "Packet":
        size, ctype, flow, device_id, user_id, sequence, opcode = _HDR.unpack_from(buf, 0)
        if len(buf) < size:
            raise ValueError("packet: faltan bytes (truncado)")
        payload = bytes(buf[HEADER:size])
        return cls(opcode, payload, ctype, flow, device_id, user_id, sequence)

    def __repr__(self) -> str:
        return (f"<Packet {self.opname} seq={self.sequence} flow={self.flow} "
                f"dev={self.device_id} usr={self.user_id} len={len(self.payload)}>")


def respond(req: Packet, opname, payload: bytes = b"") -> Packet:
    """Respuesta a `req`: eco de ctype/sequence, flow+1, ids intercambiados."""
    return Packet(opname, payload, ctype=req.ctype, flow=req.flow + 1,
                  device_id=req.user_id, user_id=req.device_id,
                  sequence=req.sequence)


def send(opname, payload: bytes = b"", device_id: int = 0, user_id: int = 0,
         sequence: int | None = None) -> Packet:
    """Trama iniciada por el servidor (p. ej. DEVICE_STATUS_GETTING_REQ).

    Replica el intercambio de ids de @agnoc/core: en el cable
    deviceId = `user_id` y userId = `device_id`.
    """
    seq = random.getrandbits(32) if sequence is None else sequence
    return Packet(opname, payload, ctype=2, flow=0,
                  device_id=user_id, user_id=device_id, sequence=seq)
