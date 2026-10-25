"""Salud de la batería, calculada por Clean Assistant con lo que ya reporta cualquier Conga
(porcentaje de batería, si está cargando, estado, m² y minutos de la limpieza). Idea de
@mactron254 (issue #12), al estilo de Vacuum_Agent, con un cálculo propio y explicable:

- Ciclos: suma de todas las bajadas de batería sin cargar; 100 % = 1 ciclo.
- Consumo por limpieza: (batería al empezar - mínima) / m² y / minutos. Solo limpiezas "limpias":
  sin recargas a mitad, >= 5 % de batería, >= 3 m² y >= 5 min.
- Velocidad de carga: %/min por debajo del 80 % (carga rápida) y por encima (fase lenta).
- Salud estimada: consumo por m² de las 5 PRIMERAS limpiezas válidas / el de las 5 ÚLTIMAS.
  Si ahora gasta más por m², la batería rinde menos. Se muestra a partir de 10 limpiezas válidas.

Se guarda en battery_health.json (no se borra al vaciar el historial).
"""
from __future__ import annotations
import json
import statistics
import time

KEEP = 200              # registros de limpiezas/cargas guardados
MIN_CLEANS = 10         # limpiezas válidas para estimar la salud
WINDOW = 5              # limpiezas para la línea base y para "lo reciente"
CC_LIMIT = 80           # por debajo, fase de carga rápida (corriente constante)
ACTIVE = ("cleaning", "mapping", "paused", "returning")


class BatteryHealth:
    def __init__(self, path: str):
        self.path = path
        self.data = {"discharged_pct": 0.0, "cleans": [], "charges": []}
        self._last = None          # (porcentaje, cargando) de la última muestra
        self._clean = None         # limpieza en curso
        self._charge = None        # carga en curso
        self._dirty_ts = 0.0
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except Exception:
            pass

    # ---------------- muestras ----------------
    def update(self, state, now: float | None = None) -> bool:
        """Nueva muestra del estado del robot. Devuelve True si cambió el resumen."""
        now = now or time.time()
        pct = getattr(state, "battery", None)
        if not isinstance(pct, (int, float)) or not getattr(state, "online", True):
            return False
        charging = bool(getattr(state, "charging", False))
        st = getattr(state, "state", None)
        changed = False

        # ciclos: bajadas sin cargar (se ignoran saltos raros de más de 20 puntos)
        if self._last is not None:
            last_pct, last_chg = self._last
            if not charging and not last_chg and 0 < last_pct - pct <= 20:
                self.data["discharged_pct"] += last_pct - pct
                self._mark()
        self._last = (pct, charging)

        # limpieza en curso
        if st in ("cleaning", "mapping") and self._clean is None and not charging:
            self._clean = {"ts": now, "start": pct, "min": pct, "area": 0.0, "minutes": 0,
                           "recharged": False}
        if self._clean is not None:
            c = self._clean
            if st in ACTIVE and not charging:
                c["min"] = min(c["min"], pct)
                c["area"] = max(c["area"], float(getattr(state, "area", 0) or 0))
                c["minutes"] = max(c["minutes"], int(getattr(state, "clean_time", 0) or 0))
            elif st in ACTIVE and charging:
                c["recharged"] = True                  # recarga a mitad: no sirve para medir
            else:                                      # en la base: fin de la limpieza
                changed |= self._close_clean(now)

        # carga en curso: tiempo que tarda cada punto, por debajo y por encima del 80 %
        if charging:
            if self._charge is None:
                self._charge = {"ts": now, "from": pct, "pct": pct, "t": now,
                                "low_pct": 0, "low_s": 0.0, "high_pct": 0, "high_s": 0.0}
            else:
                g = self._charge
                if pct > g["pct"]:
                    dt, gained = now - g["t"], pct - g["pct"]
                    zone = "low" if g["pct"] < CC_LIMIT else "high"
                    g[zone + "_pct"] += gained
                    g[zone + "_s"] += dt
                    g["pct"], g["t"] = pct, now
        elif self._charge is not None:
            changed |= self._close_charge(now)
        if self._charge is not None and pct >= 100:
            changed |= self._close_charge(now)
        self._save_if_dirty(now)
        return changed

    def _close_clean(self, now: float) -> bool:
        c, self._clean = self._clean, None
        used = c["start"] - c["min"]
        if c["recharged"] or used < 5 or c["area"] < 3 or c["minutes"] < 5:
            return False
        self.data["cleans"] = (self.data["cleans"] + [{
            "ts": int(c["ts"]), "used": used, "area": round(c["area"], 2), "minutes": c["minutes"],
            "pct_m2": round(used / c["area"], 4), "pct_min": round(used / c["minutes"], 4)}])[-KEEP:]
        self._mark(force=True)
        return True

    def _close_charge(self, now: float) -> bool:
        g, self._charge = self._charge, None
        if g["low_pct"] + g["high_pct"] < 10:
            return False
        rec = {"ts": int(g["ts"]), "from": g["from"], "to": g["pct"]}
        if g["low_pct"] >= 5 and g["low_s"] > 0:
            rec["low_rate"] = round(g["low_pct"] / (g["low_s"] / 60), 3)
        if g["high_pct"] >= 5 and g["high_s"] > 0:
            rec["high_rate"] = round(g["high_pct"] / (g["high_s"] / 60), 3)
        self.data["charges"] = (self.data["charges"] + [rec])[-KEEP:]
        self._mark(force=True)
        return True

    # ---------------- persistencia ----------------
    def _mark(self, force: bool = False):
        self._dirty_ts = self._dirty_ts or time.time()
        if force:
            self._save()

    def _save_if_dirty(self, now: float):
        if self._dirty_ts and now - self._dirty_ts > 600:   # ciclos: a disco cada 10 min
            self._save()

    def _save(self):
        self._dirty_ts = 0.0
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
        except Exception:
            pass

    # ---------------- resumen ----------------
    def summary(self) -> dict:
        cleans = self.data["cleans"]
        recent = cleans[-WINDOW:]
        med = lambda xs: round(statistics.median(xs), 3) if xs else None
        health = None
        if len(cleans) >= MIN_CLEANS:
            base, now = med([c["pct_m2"] for c in cleans[:WINDOW]]), med([c["pct_m2"] for c in recent])
            if base and now:
                health = max(0, min(100, round(base / now * 100)))
        charges = self.data["charges"][-WINDOW:]
        return {
            "cycles": round(self.data["discharged_pct"] / 100, 1),
            "health": health,
            "valid_cleans": len(cleans), "needed_cleans": MIN_CLEANS,
            "pct_per_m2": med([c["pct_m2"] for c in recent]),
            "pct_per_min": med([c["pct_min"] for c in recent]),
            "charge_rate_low": med([c["low_rate"] for c in charges if "low_rate" in c]),
            "charge_rate_high": med([c["high_rate"] for c in charges if "high_rate" in c]),
        }
