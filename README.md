# gen_pulse_owon

Control del generador de pulsos **OWON DGE2070** por USB (SCPI sobre bulk, modo "PC"):

- `owon_dge/device.py` – API local (`Dge2070`): `get_state`, `set_pulse`, `set_output`, `raw`.
- `owon_dge/server.py` – servidor HTTP/JSON (`python -m owon_dge.server`, localhost por defecto; `--token` si se abre a la red).
- `owon_dge/client.py` – `RemoteDge2070`, misma interfaz que `Dge2070`.
- `owon_dge/gui.py` – panel PyQt6 (`PulseGeneratorPanel`), embebible (p. ej. en senda) o ejecutable: `python -m owon_dge.gui [--url http://host:8750]`.
- `99-owon-dge.rules` – regla udev (copiar a `/etc/udev/rules.d/`).

Instalación: `pip install pyusb PyQt6`. Manuales de OWON (no incluidos): `docs/`.

## Particularidades verificadas en el equipo
- Enviar tiempos como `15ns`, no `1.5e-08` (en el límite, el equipo ignora la notación científica).
- `SYST:ERR?` no es fiable; verificar releyendo. No enviar nada justo tras `*CLS`.
- `DATA:DATA? EMEMory` → `#516384` + 8192 puntos uint16 big-endian (14 bit). **Escribir** formas de onda
  por SCPI en modo PC no funciona y puede colgar el USB (requiere reconectar). Pendiente.
