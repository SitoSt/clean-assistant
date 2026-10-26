"""Aviso de limpieza de sensores (precipicio, bumper, cámara/láser, contactos de carga).
Idea de @mactron254 (issue #12): la app oficial de la gen. 3xxx/4xxx recuerda limpiarlos cada
~35 h de trabajo. El robot no lo cuenta, así que lo cuenta Clean Assistant (sirve para todos los
modelos): suma el tiempo limpiando/mapeando desde el último "Hecho".

Se guarda en sensor_care.json.
"""
from __future__ import annotations
import json
import time

LIMIT_H = 35.0
ACTIVE = ("cleaning", "mapping")
MAX_GAP_S = 300          # huecos mayores (desconexión, reinicio) no cuentan como trabajo
SAVE_EVERY_S = 60


class SensorCare:
    def __init__(self, path: str, limit_h: float = LIMIT_H):
        self.path = path
        self.limit_h = limit_h
        self.data = {"seconds": 0.0, "reset_ts": None}
        self._last_ts = None
        self._saved_ts = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except Exception:
            pass

    def update(self, state, now: float | None = None) -> bool:
        """Nueva muestra del estado. Devuelve True si cambió el resumen (décimas de hora o aviso)."""
        now = now or time.time()
        active = getattr(state, "online", True) and getattr(state, "state", None) in ACTIVE
        if not active:
            self._last_ts = None
            return False
        before = self.summary()
        if self._last_ts is not None and 0 < now - self._last_ts <= MAX_GAP_S:
            self.data["seconds"] += now - self._last_ts
        self._last_ts = now
        if now - self._saved_ts >= SAVE_EVERY_S:
            self._save(now)
        return self.summary() != before

    def reset(self, now: float | None = None):
        now = now or time.time()
        self.data = {"seconds": 0.0, "reset_ts": int(now)}
        self._last_ts = now if self._last_ts is not None else None
        self._save(now)

    def summary(self) -> dict:
        hours = self.data["seconds"] / 3600
        return {"hours": round(hours, 1), "limit": self.limit_h,
                "due": hours >= self.limit_h, "reset_ts": self.data.get("reset_ts")}

    def _save(self, now):
        self._saved_ts = now
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
        except Exception:
            pass
