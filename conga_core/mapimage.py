"""Mapa -> imagen PNG (para publicarla por MQTT como entidad `image` de Home Assistant).

Sin dependencias: el PNG se escribe a mano (zlib), como el resto de CA. Parte del mapa que ya
construye `map.build_map` (rejilla recortada en `cells_b64`, habitaciones, base, robot, zonas) y
aplica la MISMA vista que la interfaz (giro `rot` 0..3 y volteo `flip`, ver s2v/v2s en index.html),
para que la imagen salga igual que en Clean Assistant.

Devuelve también la **calibración** píxel <-> metros (3 puntos, formato de
lovelace-xiaomi-vacuum-map-card), para tarjetas de mapa interactivas.
"""
from __future__ import annotations
import base64
import struct
import zlib

# colores de la interfaz (tema claro, index.html): habitaciones --r1..--r8, paredes --ink,
# base/robot --accent, paredes virtuales --critical. Fondo (celda desconocida) transparente.
ROOM_COLORS = ["#d9efe9", "#f4ecda", "#e6e9f5", "#e3efdd", "#dcecf1", "#ece5f3", "#edf1ef", "#f3e1e3"]
FLOOR = "#edf1ef"          # suelo sin habitación con nombre (segmentos temporales)
WALL = "#171c1b"
ACCENT = "#0e9c86"
ACCENT_DARK = "#096251"
CRITICAL = "#d76a5a"
TRAIL = "#0e9c86"
TARGET_PX = 800            # lado mayor aproximado de la imagen


def _rgba(hexcolor: str, a: int = 255) -> bytes:
    h = hexcolor.lstrip("#")
    return bytes((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a))


def _view_dims(W: int, H: int, rot: int):
    return (H, W) if rot % 2 else (W, H)


def _s2v(cx: float, cy: float, W: int, H: int, rot: int, flip: int):
    """Celda (recortada, continua) -> coordenadas de vista. Igual que s2v() de la interfaz."""
    x = (W - cx) if flip else cx
    y = cy
    if rot == 1:
        return H - y, x
    if rot == 2:
        return W - x, H - y
    if rot == 3:
        return y, W - x
    return x, y


def _v2s_px(vx: int, vy: int, W: int, H: int, rot: int, flip: int):
    """Píxel de vista -> celda de origen (versión entera de v2s() de la interfaz)."""
    if rot == 1:
        x, y = vy, H - 1 - vx
    elif rot == 2:
        x, y = W - 1 - vx, H - 1 - vy
    elif rot == 3:
        x, y = W - 1 - vy, vx
    else:
        x, y = vx, vy
    return (W - 1 - x) if flip else x, y


def _png(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class _Canvas:
    def __init__(self, w: int, h: int, base: bytearray):
        self.w, self.h, self.buf = w, h, base

    def dot(self, x: float, y: float, r: float, color: bytes):
        x0, x1 = int(x - r), int(x + r) + 1
        y0, y1 = int(y - r), int(y + r) + 1
        r2 = r * r
        for py in range(max(0, y0), min(self.h, y1)):
            for px in range(max(0, x0), min(self.w, x1)):
                if (px + 0.5 - x) ** 2 + (py + 0.5 - y) ** 2 <= r2:
                    i = (py * self.w + px) * 4
                    self.buf[i:i + 4] = color

    def line(self, a, b, width: float, color: bytes):
        (x0, y0), (x1, y1) = a, b
        n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / max(1.0, width / 2)))
        for i in range(n + 1):
            t = i / n
            self.dot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, color)


def render_png(m: dict, view: dict | None = None, live: bool = False, trail=None,
               scale: int | None = None):
    """Mapa (dict de build_map) -> (bytes PNG, atributos con calibración).

    live=True añade la posición del robot y el recorrido (`trail`, en metros)."""
    view = view or {}
    rot, flip = int(view.get("rot", 0)) % 4, 1 if view.get("flip") else 0
    bx, by, W, H = m["bbox"]
    cells = base64.b64decode(m["cells_b64"])
    K = scale or max(1, min(4, TARGET_PX // max(W, H, 1)))
    Wv, Hv = _view_dims(W, H, rot)

    lut = [_rgba("#000000", 0)] * 256
    for v in range(2, 255):
        lut[v] = _rgba(FLOOR)
    lut[1] = lut[255] = _rgba(WALL)
    for i, r in enumerate(m.get("rooms") or []):
        rid = r.get("id")
        if isinstance(rid, int) and 1 < rid < 255:
            lut[rid] = _rgba(ROOM_COLORS[i % len(ROOM_COLORS)]) if r.get("named", True) else _rgba(FLOOR)

    rows = []
    for vy in range(Hv):
        px = []
        for vx in range(Wv):
            sx, sy = _v2s_px(vx, vy, W, H, rot, flip)
            px.append(lut[cells[sy * W + sx]] * K)
        row = b"".join(px)
        rows.append(row * K)
    img_w, img_h = Wv * K, Hv * K
    cv = _Canvas(img_w, img_h, bytearray(b"".join(rows)))

    def to_px(cx, cy):
        vx, vy = _s2v(cx, cy, W, H, rot, flip)
        return vx * K, vy * K

    w = m.get("world") or {"min_x": -20.0, "min_y": -20.0, "res": 0.05}

    def world_to_px(wx, wy):
        return to_px((wx - w["min_x"]) / w["res"] - bx, (wy - w["min_y"]) / w["res"] - by)

    for z in m.get("stored_zones") or []:                 # paredes/zonas virtuales del robot
        pts = [to_px(x, y) for x, y in z.get("points") or []]
        if len(pts) == 2:
            cv.line(pts[0], pts[1], max(2, K), _rgba(CRITICAL))
        elif len(pts) > 2:
            for a, b in zip(pts, pts[1:] + pts[:1]):
                cv.line(a, b, max(2, K * 0.75), _rgba(CRITICAL))
    if live and trail:
        tp = [world_to_px(x, y) for x, y in trail]
        for a, b in zip(tp, tp[1:]):
            cv.line(a, b, max(2, K * 0.8), _rgba(TRAIL, 170))
    ch = m.get("charger")
    if ch and ch.get("x") is not None:
        x, y = to_px(ch["x"], ch["y"])
        cv.dot(x, y, K * 3 + 2, _rgba("#ffffff"))
        cv.dot(x, y, K * 3, _rgba(ACCENT))
    rb = m.get("robot")
    if live and rb and rb.get("x") is not None:
        x, y = to_px(rb["x"], rb["y"])
        cv.dot(x, y, K * 4 + 2, _rgba("#ffffff"))
        cv.dot(x, y, K * 4, _rgba(ACCENT_DARK))

    # calibración: 3 esquinas de la rejilla, en metros (mundo) y en píxeles de la imagen
    calib = []
    for cx, cy in ((0, 0), (W, 0), (0, H)):
        wx = (cx + bx) * w["res"] + w["min_x"]
        wy = (cy + by) * w["res"] + w["min_y"]
        px, py = to_px(cx, cy)
        calib.append({"vacuum": {"x": round(wx, 3), "y": round(wy, 3)},
                      "map": {"x": round(px, 1), "y": round(py, 1)}})
    attrs = {"calibration_points": calib, "width": img_w, "height": img_h, "scale": K,
             "rotation": rot * 90, "flip": bool(flip), "live": bool(live),
             "units": "m", "map_name": m.get("name")}
    return _png(img_w, img_h, bytes(cv.buf)), attrs
