# Handoff: integrar el control del OWON DGE2070 en senda

Documento para una sesión nueva de Claude Code. Objetivo del usuario: llevar el panel del generador de
pulsos a **`/home/colina/senda`** (software DAQ `colina_daq`, PyQt6). Este repo
(`/home/colina/gen_pulse`, remoto `https://github.com/Aretno/gen_pulse_owon.git`, rama `main`) contiene
la librería ya probada con el equipo real. Idioma de trabajo: **español**.

## 1. Estado: qué está hecho y verificado en el equipo real

Generador **OWON DGE2070** (serie 24480469, firmware V1.0.4.3.0), conectado por USB a este PC.

| Pieza | Fichero | Estado |
|---|---|---|
| Transporte USB TMC (recomendado) | `owon_dge/tmc.py` | verificado |
| Transporte modo "PC" (protocolo OWON) | `owon_dge/transport.py` | verificado |
| Elección automática de transporte | `transport.auto_transport()` | verificado |
| API de alto nivel `Dge2070` | `owon_dge/device.py` | verificado en ambos canales |
| Servidor HTTP/JSON (stdlib) | `owon_dge/server.py` | verificado |
| Cliente remoto `RemoteDge2070` (misma interfaz) | `owon_dge/client.py` | verificado |
| Panel PyQt6 embebible `PulseGeneratorPanel` | `owon_dge/gui.py` | verificado (offscreen + escritura real) |
| Regla udev (modos TMC y PC) | `99-owon-dge.rules` | instalada en este PC |

Verificado con escrituras reales y restauración: frecuencia, anchura, ciclo de trabajo, nivel alto/bajo,
flancos de subida/bajada y salida ON/OFF, en **canal 1 y canal 2**. Lectura de la forma de onda
(`read_waveform`, 8192 puntos) solo en modo TMC.

**NO está hecho** (pendiente, ver §6): cargar formas de onda arbitrarias.

## 2. API que se va a reutilizar

```python
from owon_dge import Dge2070, RemoteDge2070           # misma interfaz
dev = Dge2070()                                       # USB local (auto TMC/PC)
dev = RemoteDge2070("http://host:8750", token=None)   # vía servidor HTTP

dev.get_state(ch)            # dict: function, freq, period, width, duty, high, low, lead, trail,
                             #       output, impedance, idn  (+ burst* con full=True)
dev.set_pulse(ch, freq=..., width=..., high=..., low=..., lead=..., trail=...)  # SI: Hz, s, V
dev.set_pulse(ch, period=...)   # = 1/freq;  width y duty son excluyentes
dev.set_output(ch, True)
dev.read_waveform()          # solo TMC
dev.raw("SOUR1:FREQ?")       # SCPI en bruto
dev.close()                  # IMPORTANTE: libera el USB (y reatacha usbtmc en modo TMC)
```

Panel: `PulseGeneratorPanel(backend, channel=1, poll_ms=2000)` es un `QWidget`; las llamadas al backend
van en un `QThread` propio (no bloquea la GUI), consulta el estado cada `poll_ms` salvo mientras el
usuario edita, y su `closeEvent` para timer e hilo. Unidades en pantalla: MHz, ns, V.

Servidor: `python -m owon_dge.server [--host 127.0.0.1] [--port 8750] [--token T]`
(solo localhost por defecto; con otra IP exige `--token`). Endpoints: `GET /api/idn`,
`GET|PUT /api/channels/<1|2>`, `POST /api/channels/<n>/output {"on":bool}`, `GET /api/waveform`,
`POST /api/raw {"cmd":..}`.

## 3. Particularidades del equipo (aprendidas con pruebas; no las redescubras)

1. **Tiempos con sufijo `ns`** (`15ns`, `976ns`), NO `1.5e-08`: en notación científica el equipo
   **ignora en silencio** los valores justo en el límite (flanco mínimo 15 ns, anchura máxima
   = ciclo 97,6 % a 1 MHz). Ya resuelto en `device._fmt`. La anchura exactamente en su máximo solo
   entra como `PULS:DCYC`; `set_pulse` lo hace solo (`_width_via_duty`).
2. **Anchura, ciclo y frecuencia están ligados** (doc SCPI): cambiar uno recalcula otros. Límites:
   duty ≥ 100·minW/T y ≤ 100·(1−2·minW/T). Por eso `set_pulse` fija freq → width/duty → niveles → flancos
   y **verifica cada escritura releyendo** (y reintenta hasta 3 veces); lanza `DeviceError` si no entra.
3. **`SYST:ERR?` no es fiable** (error −102 «pegajoso» de consultas no soportadas): no se usa para validar.
   **No enviar nada justo después de `*CLS`** (se pierde).
4. Consultas **no soportadas** (dan timeout): `FUNC?`, `PULS:PER?` (periodo = 1/freq), `PULS:DEL?`,
   `…? MIN|MAX`.
5. **Respuestas vacías esporádicas** (~0,5 %) en TMC: `query_bytes` las reintenta.
6. `OUTPn:IMP?` devuelve `9.9E+37` = alta impedancia (se devuelve como cadena).
7. **Un solo programa puede tener abierto el USB.** El servidor o la GUI local lo retienen mientras
   viven. Si dos procesos lo abren → `Resource busy`.
8. **Reiniciar/reconectar el generador apaga la salida CH1** (estaba ON) y los parámetros se conservan.
   Ahora mismo **ambos canales están en OFF**.
9. Rendimiento TMC: consulta ~15 ms, `get_state` ~0,22 s, escritura ~0,38 s por parámetro.
   En modo PC: ×6 más lento y puede **colgarse** si se envían bloques grandes (requiere
   reconectar el USB físicamente).

## 4. Dos modos USB del generador (panel frontal: Utility → System → USB Dev)

- **USB TMC** → `5345:1235`, el kernel crea `/dev/usbtmc0`. **Modo actual y recomendado.**
  `tmc.py` desacopla el driver `usbtmc` y habla TMC con pyusb porque `/dev/usbtmc0` solo entrega los
  primeros bytes de bloques largos (bug del equipo/driver: «wrong bTag» en dmesg).
  `close()` reatacha el driver.
- **PC** → `5345:1234`, protocolo interno de OWON, el que usa el *Waveform Editor* (Windows).

`99-owon-dge.rules` (en `/etc/udev/rules.d/`) da permisos a ambos modos.

## 5. Contexto de senda (lo que ya he mirado de `/home/colina/senda`)

- Paquete `colina_daq`, GUI en `colina_daq/gui/`, **PyQt6 + pyqtgraph**, Python ≥3.10, venv propio
  `.venv` (3.12.15, PyQt6 6.11.0), **sin pyusb instalado**.
- La ventana (`gui/window.py`) crea las pestañas del panel izquierdo en `_build_left_panel`:
  `Trigger, Filtro, Grabación, Master, Planes`; cada una es un *mixin* (`tab_*.py`, p. ej.
  `tab_master.py`: `class MasterTabMixin` con `_build_master_tab() -> QScrollArea`).
  Estilo: `QGroupBox`/`QFormLayout`, márgenes 3–6 px, `theme.py` (claro/oscuro), textos en español.
- Configuración en `colina_daq/config.py` (dataclasses: `HVConfig`, `NotificationConfig`, …) y JSON
  `config_*.json` en la raíz. Precedente de control remoto: `hv_control.py`/`hv_remote.py` (túnel SSH,
  cliente de socket); la HV solo se **lee**.
- `pyproject.toml` con extras (`gui`, `hdf5`, `analysis`, `dev`) y **lockfiles** en `requirements/`
  (`lock-wsl.txt`, `lock-windows.txt`, generados con `pip-compile`). Senda también corre en WSL/Windows,
  donde la regla udev no existe y libusb necesita driver (WinUSB/Zadig) → decidir si el generador se
  soporta solo en Linux o se usa el cliente remoto (HTTP), que no necesita pyusb.
- Documentación: `README.md`, `PROJECT_CONTEXT.md`, `docs/MANUAL_GUI.md` + HTML en `docs/`.
- Estado git de senda al revisar: rama con commits propios y `daq_tarjeta_marc.json` sin seguir (no es mío).

## 6. Pendiente futuro (aparcado a petición del usuario)

**Cargar formas de onda arbitrarias** (lo que hace el *Waveform Editor* de OWON). Hallazgos:
- Lectura: `DATA:DATA? EMEMory` → `#516384` + **8192 puntos uint16 big-endian, 14 bit (0..16383)**.
  Copia de la memoria original en `backups/emem_original.bin` (una rampa).
- Escritura `DATA:DATA EMEMory,#516384<datos>`: **sin efecto** ni en TMC (un solo mensaje, sin cuelgue)
  ni en PC (además **colgó** el USB dos veces). El Waveform Editor debe de usar otro mecanismo
  (el manual habla de ficheros USER0–USER15 y «File transfer completed»).
- Plan: capturar con Wireshark+usbmon el tráfico del Waveform Editor (Windows/Wine, modo PC) al escribir
  una onda y replicarlo. **No mandar bloques grandes al equipo sin avisar al usuario.**

## 7. Propuesta para la integración (a validar con el usuario)

1. **Cómo se incluye el código.** Opciones: (a) dependencia vía git
   (`owon-dge @ git+https://github.com/Aretno/gen_pulse_owon.git`) como extra opcional `pulsegen`;
   (b) copiar `owon_dge/` dentro de senda. Recomiendo (a) para no duplicar, con extra
   `pulsegen = ["owon-dge", "pyusb"]` y regenerar los lockfiles. `owon_dge` no tiene dependencias
   duras (pyusb solo en la máquina con el USB; PyQt6 solo para la GUI).
2. **Pestaña nueva** `tab_pulsegen.py` (`PulseGenTabMixin`, patrón de `tab_master.py`) que envuelva
   `PulseGeneratorPanel` y se añada en `_build_left_panel`. Adaptar el estilo con `theme.py`.
3. **Configuración**: dataclass `PulseGenConfig` en `config.py` (`enabled`, `mode: local|remote`,
   `url`, `token`, `channel`) como `HVConfig`; la pestaña debe degradarse con elegancia si no hay
   generador o falta pyusb (mensaje, sin romper el arranque del DAQ).
4. **Ciclo de vida**: crear el backend al abrir la pestaña/conectar, `dev.close()` al cerrar la
   ventana (el USB debe liberarse). Un backend por proceso.
5. **Modo remoto** (cliente HTTP + servidor en el PC con el generador, por túnel SSH como la HV)
   si la GUI no corre en la máquina del USB.
6. Tests (`tests/`, pytest, hay `tests/test_plot_channels.py` de ejemplo) con un backend simulado
   (clase falsa con `get_state/set_pulse/set_output`) — **no** tocar el equipo real en tests.
7. Documentar en `README.md`/`docs/MANUAL_GUI.md` de senda (regla udev, modo USB TMC del panel frontal).

### Preguntas que conviene hacer al usuario al empezar
- ¿La GUI de senda corre en el mismo PC que tiene el generador, o hace falta el modo remoto?
- ¿Solo Linux o también WSL/Windows? (libusb/driver en Windows)
- ¿Dependencia git (a) o copia del código (b)? ¿Subir cambios de senda a su remoto o solo local?
- ¿Control de ambos canales en una sola pestaña (el panel actual tiene un selector de canal) o dos paneles?
- ¿Algún acoplamiento con el DAQ (p. ej. registrar los parámetros del pulso en el run/logbook,
  o planes que fijen el pulso antes de adquirir)? Hoy el panel es independiente.

## 8. Cómo probar sin riesgo

```bash
cd /home/colina/gen_pulse
.venv/bin/python -m owon_dge.server &          # abre el USB
PYTHONPATH=. .venv/bin/python -c "from owon_dge import RemoteDge2070 as R; print(R().get_state(1))"
# GUI suelta:  .venv/bin/python -m owon_dge.gui [--url http://127.0.0.1:8750]
# Parar el servidor para liberar el USB:  fuser -k 8750/tcp
```
- El generador es del usuario y puede estar en uso: **las escrituras cambian la señal real**. Antes de
  escribir, lee el estado y **restáuralo** al terminar. Con `QT_QPA_PLATFORM=offscreen` se prueba la GUI
  sin pantalla.
- Cuidado con `pkill -f owon_dge.server`: puede matar tu propia shell; usa `fuser -k 8750/tcp`.
- Documentos de OWON (no están en git por copyright): `docs/DGE2000_3000_SCPI_Protocol.pdf` y
  `docs/DGE2000_Arbitrary_Waveform_Generator_User_Manual.pdf` en este directorio (`pdftotext -layout`).
- Memoria persistente del proyecto con las particularidades:
  `~/.claude/projects/-home-colina-gen-pulse/memory/owon-dge2070-quirks.md`.
