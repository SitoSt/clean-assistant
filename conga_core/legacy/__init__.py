"""Backend 'legacy' para la generación 3irobotix 3xxx/4xxx (Conga 3090/3490/4090…).

Esta generación NO usa el stack TLS+WebSocket+JSON del 8090/9090 (que implementa
`conga_core.robot.RealRobot`). Habla un protocolo propio: TCP en claro sobre tres
puertos — 4010 (comandos), 4030 (mapa) y 4050 (hora/rtc) — con una trama binaria de
24 bytes de cabecera + payload Protobuf. El robot inicia la conexión saliente hacia
la nube, así que Clean Assistant hace de "nube local" (mismo patrón que cloud_stub).

Spec reverseada a partir de `@agnoc/core` (MIT, congatudo/agnoc), probada en los
Conga 3090/3490/4090, y confirmada por la ingeniería inversa de @mactron254 (issue
#12): coinciden puertos, trama de 24 bytes y la mayoría de opcodes.

`LegacyRobot` expondrá la MISMA interfaz que `RealRobot`/`MockRobot`
(`start()`, `command(control)`, `tick()`, `.state`, `.map`, `.pose` + callbacks),
de modo que el resto del add-on (app.py, MQTT, UI) no cambia.
"""
