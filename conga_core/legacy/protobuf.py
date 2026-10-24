"""Códec Protobuf mínimo, sin dependencias — al estilo del parser de map.py.

El payload de cada trama legacy es un mensaje Protobuf. En vez de arrastrar la
librería `protobuf` (y protoc en build) codificamos/decodificamos el *wire format*
directamente a/desde dicts por número de campo, como ya hace conga_core/map.py.

decode(buf) -> {num: [valores]}  (siempre listas, por si el campo es `repeated`)
encode(dict|items) -> bytes      (int/bool->varint, float->32-bit, bytes/str/dict->len-delim)

Tipos de wire Protobuf: 0=varint, 1=64-bit, 2=len-delimited, 5=32-bit.
"""
from __future__ import annotations
import struct


def read_varint(buf, pos: int):
    """Lee un varint base-128 desde buf[pos]. Devuelve (valor, nueva_pos)."""
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def write_varint(n: int) -> bytes:
    """Codifica un entero como varint (se trata como uint64 sin signo)."""
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode(buf: bytes) -> dict:
    """Decodifica un mensaje Protobuf a {num_campo: [valores]}.

    varint/32/64 -> int; len-delimited -> bytes (el llamador decide si es
    string, sub-mensaje con decode() otra vez, o bytes crudos).
    """
    pos, n = 0, len(buf)
    out: dict[int, list] = {}
    while pos < n:
        key, pos = read_varint(buf, pos)
        field, wt = key >> 3, key & 7
        if wt == 0:                       # varint
            val, pos = read_varint(buf, pos)
        elif wt == 2:                     # length-delimited
            ln, pos = read_varint(buf, pos)
            val = bytes(buf[pos:pos + ln])
            pos += ln
        elif wt == 1:                     # 64-bit
            val = int.from_bytes(buf[pos:pos + 8], "little")
            pos += 8
        elif wt == 5:                     # 32-bit
            val = int.from_bytes(buf[pos:pos + 4], "little")
            pos += 4
        else:
            raise ValueError(f"protobuf: wire type no soportado ({wt})")
        out.setdefault(field, []).append(val)
    return out


def _encode_field(out: bytearray, num: int, val) -> None:
    if isinstance(val, (list, tuple)):            # repeated
        for v in val:
            _encode_field(out, num, v)
        return
    if isinstance(val, bool):                     # bool antes que int
        out += write_varint((num << 3) | 0)
        out += write_varint(1 if val else 0)
    elif isinstance(val, int):                    # varint
        out += write_varint((num << 3) | 0)
        out += write_varint(val)
    elif isinstance(val, float):                  # float (32-bit; coordenadas del mapa)
        out += write_varint((num << 3) | 5)
        out += struct.pack("<f", val)
    else:                                         # len-delimited
        if isinstance(val, str):
            data = val.encode("utf-8")
        elif isinstance(val, dict):
            data = encode(val)                    # sub-mensaje
        elif isinstance(val, (bytes, bytearray)):
            data = bytes(val)
        else:
            raise TypeError(f"protobuf: tipo no codificable: {type(val)!r}")
        out += write_varint((num << 3) | 2)
        out += write_varint(len(data))
        out += data


def encode(fields) -> bytes:
    """Codifica un dict {num: valor} (o lista de (num, valor)) a bytes Protobuf.

    valor: int/bool -> varint; str/bytes/dict -> len-delimited; list -> repeated.
    """
    items = fields.items() if isinstance(fields, dict) else fields
    out = bytearray()
    for num, val in items:
        _encode_field(out, num, val)
    return bytes(out)


# ---------------- helpers de lectura ----------------
def first(decoded: dict, num: int, default=None):
    """Primer valor del campo `num` de un dict decode(), o `default`."""
    vals = decoded.get(num)
    return vals[0] if vals else default


def as_str(val) -> str:
    return val.decode("utf-8", "replace") if isinstance(val, (bytes, bytearray)) else str(val)


def as_f32(val) -> float:
    """Reinterpreta un valor de 32-bit (wt 5, leído como int) como float."""
    return struct.unpack("<f", int(val).to_bytes(4, "little"))[0]
