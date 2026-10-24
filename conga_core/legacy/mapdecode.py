"""Decodificador del mapa de la generación legacy (port de @agnoc/core map.decoder.ts).

El robot manda el mapa (DEVICE_MAPID_PUSH_MAP_INFO 0x1164 / GET_GLOBAL_INFO_RSP 0x1163)
como un stream zlib. Dentro, una `mask` dice qué secciones vienen, en este orden:
estado, cabecera+rejilla, recorrido, base, paredes virtuales, áreas, spot, pose del robot,
planes+habitaciones, conexiones entre habitaciones, ?, segmentos (píxeles) de cada habitación.
Todo little-endian; strings = longitud u8 + utf-8.

La posición del robot (0x1166) y de la base (0x1168) llegan también sueltas.
"""
from __future__ import annotations
import struct
import zlib

MASK_STATUS, MASK_MAP, MASK_HISTORY, MASK_CHARGER = 0x1, 0x2, 0x4, 0x8
MASK_WALLS, MASK_AREAS, MASK_SPOT, MASK_ROBOT = 0x10, 0x20, 0x40, 0x80
MASK_PLANS, MASK_ROOM_CONN, MASK_ROOM_ENABLE, MASK_ROOM_SEG = 0x800, 0x1000, 0x2000, 0x4000


class _R:
    """Lector secuencial little-endian (equivale a utils/stream.util de @agnoc)."""
    __slots__ = ("b", "p")

    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def _take(self, n: int) -> bytes:
        if self.p + n > len(self.b):
            raise ValueError(f"mapa: fin de datos ({self.p}+{n}>{len(self.b)})")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self):  return self._take(1)[0]
    def u16(self): return struct.unpack("<H", self._take(2))[0]
    def u32(self): return struct.unpack("<I", self._take(4))[0]
    def f32(self): return struct.unpack("<f", self._take(4))[0]
    def raw(self, n): return self._take(n)

    def s(self) -> str:
        n = self.u8()
        return self._take(n).decode("utf-8", "replace") if n else ""

    @property
    def left(self) -> int:
        return len(self.b) - self.p


def _clean_area_info(r: _R) -> dict:
    head, plan = r.u32(), r.u32()
    areas = []
    for _ in range(r.u32()):
        aid, pid = r.u32(), r.u32()
        n = r.u32()
        pts = [(r.f32(), r.f32()) for _ in range(n)]
        r.raw(3 * n * 4)                      # bytes desconocidos (como @agnoc)
        areas.append({"id": aid, "plan": pid, "points": pts})
    return {"map_head_id": head, "plan_id": plan, "areas": areas}


def _area_info_list(r: _R) -> list:
    out = []
    for _ in range(r.u32()):
        a = {"id": r.u32(), "type": r.u32(), "points": r.u32()}
        n = a["points"]
        if n:
            a["x"] = [r.f32() for _ in range(n)]
            a["y"] = [r.f32() for _ in range(n)]
            for _ in range(3):
                [r.f32() for _ in range(n)]   # unk1..unk3
        out.append(a)
    return out


def _read(r: _R, mask: int) -> dict:
    if mask > 0x7FFF:
        raise ValueError(f"mapa: mask inválida {mask}")
    d: dict = {"mask": mask}
    if mask & MASK_STATUS:
        keys = ("mapHeadId", "hasHistoryMap", "workMode", "battery", "chargeStatus",
                "type", "faultCode", "cleanPreference", "repeatClean", "cleanTime", "cleanSize")
        d["status"] = {k: r.u32() for k in keys}
    if mask & MASK_MAP:
        h = {"map_head_id": r.u32(), "valid": r.u32(), "type": r.u32(),
             "size_x": r.u32(), "size_y": r.u32(),
             "min_x": r.f32(), "min_y": r.f32(), "max_x": r.f32(), "max_y": r.f32(),
             "resolution": r.f32()}
        d["head"] = h
        d["grid"] = r.raw(h["size_x"] * h["size_y"])
    if mask & MASK_HISTORY:
        head, pose_id, n = r.u32(), r.u32(), r.u32()
        d["history"] = {"map_head_id": head, "pose_id": pose_id,
                        "points": [(r.u8(), r.f32(), r.f32()) for _ in range(n)]}
    if mask & MASK_CHARGER:
        d["charger"] = {"map_head_id": r.u32(), "x": r.f32(), "y": r.f32(), "phi": r.f32()}
    if mask & MASK_WALLS:
        d["walls"] = _clean_area_info(r)
    if mask & MASK_AREAS:
        d["areas"] = _clean_area_info(r)
    if mask & MASK_SPOT:
        d["spot"] = {"map_head_id": r.u32(), "ctrl": r.u32(),
                     "x": r.f32(), "y": r.f32(), "phi": r.f32()}
    if mask & MASK_ROBOT:
        d["robot"] = {"map_head_id": r.u32(), "pose_id": r.u32(), "update": r.u8(),
                      "x": r.f32(), "y": r.f32(), "phi": r.f32()}
    if mask & 0x700:
        # 0x100/0x200/0x400 (modo zona/spot): formato desconocido, @agnoc tampoco las lee. Lo
        # leído hasta aquí es válido; lo que sigue (planes/habitaciones) no se puede localizar.
        d["partial"] = True
        return d
    if mask & MASK_PLANS:
        d["plan_info"] = {"map_head_id": r.u32(), "mask": r.u16(), "first_clean": r.u8()}
        d["maps"] = [{"map_head_id": r.u32(), "name": r.s()} for _ in range(r.u8())]
        d["current_plan_id"] = r.u32()
        d["rooms"] = [{"id": r.u8(), "name": r.s(), "state": r.u8(), "x": r.f32(), "y": r.f32()}
                      for _ in range(r.u32())]
        plans = []
        for _ in range(r.u8()):
            p = {"id": r.u32(), "name": r.s(), "map_head_id": r.u32(), "current": r.u32()}
            p["areas"] = _area_info_list(r)
            p["rooms"] = [{"id": r.u8(), "enable": r.u8()} for _ in range(r.u32())]
            plans.append(p)
        d["plans"] = plans
    if mask & MASK_ROOM_CONN and d.get("rooms") is not None:
        rooms = d["rooms"]
        d["room_conn"] = {a["id"]: [b["id"] for b in rooms if r.u8()] for a in rooms}
    if mask & MASK_ROOM_ENABLE:
        d["room_enable"] = {"map_head_id": r.u32(), "size": r.u8()}
        r.raw(50)                             # bytes desconocidos (como @agnoc)
    if mask & MASK_ROOM_SEG:
        segs = {}
        for _ in range(r.u32()):
            rid, n = r.u32(), r.u32()
            segs[rid] = [(r.u16(), r.u16(), r.u8()) for _ in range(n)]
        d["segments"] = segs
    if r.left:
        raise ValueError(f"mapa: {r.left} bytes sin leer")
    return d


def decode_map(payload: bytes) -> dict:
    """Infla y decodifica un mapa legacy. Prueba el formato 3490 (mask u32) y si falla el
    del 3090 (mask u16), igual que @agnoc."""
    buf = zlib.decompress(payload)
    try:
        r = _R(buf)
        return _read(r, r.u32())
    except ValueError:
        r = _R(buf)
        return _read(r, r.u16())


def decode_robot_position(payload: bytes) -> dict:
    r = _R(payload)
    return {"map_head_id": r.u32(), "pose_id": r.u32(), "update": r.u8(),
            "x": r.f32(), "y": r.f32(), "phi": r.f32()}


def decode_charge_position(payload: bytes) -> dict:
    r = _R(payload)
    return {"pose_id": r.u32(), "x": r.f32(), "y": r.f32(), "phi": r.f32()}
