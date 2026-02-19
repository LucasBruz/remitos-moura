import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import re
import os
from pathlib import Path
import shutil
import io
import time
import requests

st.set_page_config(page_title="Clasificador de Remitos", page_icon="📦", layout="centered")
st.title("📦 Clasificador de Remitos - App Web (con OCR y anti-bloqueo)")
st.write("Subí un PDF; la app separa, reconoce (texto directo o OCR por API), ordena y renombra los remitos, y devuelve un ZIP.")

# === Control de OCR (antibloqueo) ===
MAX_OCR = 20             # Máximo de páginas a enviar al OCR por ejecución (ajustable)
SLEEP_BETWEEN_OCR = 1.0  # Pausa (segundos) entre llamadas OCR (ajustable)
RATE_LIMIT_MAX = 170     # Tope horario para no llegar al límite de 180/h del proveedor (margen de seguridad)

uploaded_pdf = st.file_uploader("📄 Subir PDF", type=["pdf"])
patron = st.text_input("🔍 Patrón (regex) para detectar remitos", value=r"\b\d{4}-\d{8}\b")
usar_ocr = st.checkbox("Usar OCR para páginas sin texto", value=True)
start_page = st.number_input("Continuar desde página", min_value=1, value=1, step=1)
procesar = st.button("🚀 Procesar PDF")

# ===== Estado de ventana horaria para el rate limit =====
if "window_start" not in st.session_state:
    st.session_state.window_start = None
if "ocr_calls" not in st.session_state:
    st.session_state.ocr_calls = 0

def _reset_window_if_needed():
    now = time.time()
    ws = st.session_state.window_start
    if ws is None or (now - ws) >= 3600:
        st.session_state.window_start = now
        st.session_state.ocr_calls = 0

def can_call_ocr():
    _reset_window_if_needed()
    return st.session_state.ocr_calls < RATE_LIMIT_MAX

def register_ocr_call():
    st.session_state.ocr_calls += 1

# ===== Utilidades =====

def normalizar_remito(remito: str):
    s = re.sub(r"[^0-9-]", "", remito)
    if "-" in s:
        parts = s.split("-", 1)
        if len(parts) != 2:
            return None
        suc, num = parts
    else:
        solo = re.sub(r"\D", "", s)
        if len(solo) < 12:
            return None
        suc, num = solo[:-8], solo[-8:]
    suc = suc.zfill(4)[-4:]
    num = num.zfill(8)[-8:]
    return f"{suc}-{num}"

def detectar_por_texto(texto: str, patron_rx: str):
    # 1) patrón del usuario
    if patron_rx:
        try:
            m = re.search(patron_rx, texto)
            if m:
                g = m.group(1) if m.groups() else m.group(0)
                norm = normalizar_remito(g)
                if norm:
                    return norm
        except Exception:
            pass
    # 2) heurística: bloque de 10 a 14 dígitos (sin guión)
    m2 = re.search(r"\b(\d{10,14})\b", texto)
    if m2:
        norm = normalizar_remito(m2.group(1))
        if norm:
            return norm
    # 3) fallback: dos grupos separados por no dígito
    m3 = re.search(r"(\d{1,4})\D+(\d{5,10})", texto)
    if m3:
        norm = normalizar_remito(f"{m3.group(1)}-{m3.group(2)}")
        if norm:
            return norm
    return None

def extraer_texto_por_pypdf2(reader: PdfReader, idx: int) -> str:
    try:
        page = reader.pages[idx]
        t = page.extract_text()
        return t or ""
    except Exception:
        return ""

def ocr_api_pdf_bytes(pdf_bytes: bytes, api_key: str, language="spa") -> str:
    """
    OCR en OCR.space para un PDF de 1 página.
    Maneja 403/timeout con logs y reintentos cortos.
    """
    url = "https://api.ocr.space/parse/image"
    files = {"file": ("page.pdf", pdf_bytes, "application/pdf")}
    data = {
        "language": language,
        "isOverlayRequired": False,
        "OCREngine": 2,
        "scale": True
    }
    headers = {"apikey": api_key}

    for intento in range(2):  # hasta 2 intentos rápidos
        try:
            r = requests.post(url, files=files, data=data, headers=headers, timeout=30)
            if r.status_code == 403:
                # Mostrar detalle del server (ayuda a diagnosticar plan/clave/limit)
                try:
                    st.warning(f"403 OCR: {r.json()}")
                except Exception:
                    st.warning(f"403 OCR (texto): {r.text[:300]}")
                r.raise_for_status()

            r.raise_for_status()
            js = r.json()
            if js.get("IsErroredOnProcessing"):
                raise RuntimeError(js.get("ErrorMessage") or "OCR error")

            results = js.get("ParsedResults", [])
            return (results[0].get("ParsedText", "") or "") if results else ""
        except Exception as e:
            if intento == 0:
                time.sleep(2)  # pequeño backoff y reintento
            else:
                raise

# Mostrar si la API Key está cargada (diagnóstico + contador horario)
has_key = "OCRSPACE_API_KEY" in st.secrets and bool(st.secrets["OCRSPACE_API_KEY"])
api_key = st.secrets.get("OCRSPACE_API_KEY", None)
_reset_window_if_needed()
st.caption(
    f"🔐 API Key cargada: {'Sí' if has_key else 'No'} • "
    f"OCR en esta hora: {st.session_state.ocr_calls}/{RATE_LIMIT_MAX}"
)

# ===== Flujo principal =====

if procesar and uploaded_pdf:
    with st.spinner("Procesando PDF…"):
        tmp_dir = Path("remitos_tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()

        clasificados = tmp_dir / "Remitos Clasificados"
        clasificados.mkdir()

        uploaded_pdf.seek(0)
        reader = PdfReader(uploaded_pdf)
        total = len(reader.pages)
        start_idx = max(0, min(total - 1, start_page - 1))

        registros = []

        # Progreso visual
        progress = st.progress(0, text="Inicializando…")
        status = st.empty()
        ocr_count = 0  # páginas enviadas a OCR en esta ejecución
        stopped_by_rate = False

        for i in range(start_idx, total):
            status.text(f"Procesando página {i+1} de {total}…")
            progress.progress(int((i+1)/total*100))

            # 1) Intento por texto embebido
            texto = extraer_texto_por_pypdf2(reader, i)
            remito = detectar_por_texto(texto, patron)

            # 2) OCR solo si:
            #   - no se detectó por texto
            #   - el checkbox está activo
            #   - existe API Key
            #   - no superamos MAX_OCR
            #   - y estamos debajo del RATE_LIMIT_MAX horario
            if (not remito) and usar_ocr and api_key and (ocr_count < MAX_OCR):
                if not can_call_ocr():
                    faltan = 3600 - int(time.time() - st.session_state.window_start)
                    st.info(f"⏳ Llegaste al cupo horario de OCR ({RATE_LIMIT_MAX}/h). "
                            f"Volvé a ejecutar en ~{max(1, faltan)} segundos o más.")
                    stopped_by_rate = True
                    break

                # Generar PDF de 1 página en memoria
                buf = io.BytesIO()
                w = PdfWriter()
                w.add_page(reader.pages[i])
                w.write(buf)
                buf.seek(0)

                try:
                    texto_ocr = ocr_api_pdf_bytes(buf.read(), api_key, language="spa")
                    remito = detectar_por_texto(texto_ocr, patron)
                except Exception as e:
                    st.warning(f"⚠️ OCR falló en la página {i+1}: {e}")

                # Registrar llamada y pausar
                register_ocr_call()
                ocr_count += 1
                time.sleep(SLEEP_BETWEEN_OCR)

            # 3) Guardar la página individual siempre
            writer = PdfWriter()
            writer.add_page(reader.pages[i])

            if remito:
                nombre = f"{remito}.pdf"
                registros.append((remito, nombre))
            else:
                sufijo = ""
                if (not remito) and usar_ocr and api_key and (ocr_count >= MAX_OCR):
                    sufijo = "_TOPE_OCR"
                nombre = f"SIN_REMITO_{i+1}{sufijo}.pdf"

            with open(clasificados / nombre, "wb") as f:
                writer.write(f)

        # Orden por sucursal y número (numérico)
        registros.sort(key=lambda x: tuple(map(int, x[0].split('-'))))

       # Sin prefijo: dejamos los nombres tal cual (0034-00033477.pdf, etc.)
# Si querés conservar un orden estable dentro del ZIP, el ZIP se construirá en base al orden del listado.
# No renombramos con índice.
pass

        # Armar ZIP con carpeta "Remitos Clasificados"
        zip_path = tmp_dir / "remitos_clasificados.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for rootp, _, files in os.walk(clasificados):
                for f in files:
                    abs_path = Path(rootp) / f
                    z.write(abs_path, abs_path.relative_to(tmp_dir))

        st.success("✔ Remitos procesados correctamente")
        with open(zip_path, "rb") as zf:
            st.download_button(
                "📥 Descargar ZIP",
                zf,
                file_name="remitos_clasificados.zip",
                mime="application/zip",
            )

        # Mensajes de cierre
        if stopped_by_rate:
            st.info("👆 Se detuvo por el límite horario de OCR. "
                    "Usá “Continuar desde página” para retomar luego donde quedó.")
        elif usar_ocr and api_key and (ocr_count >= MAX_OCR):
            st.info("🔁 Alcanzaste el tope de páginas OCR por esta ejecución. "
                    "Aumentá MAX_OCR o re-ejecutá con 'Continuar desde página' para seguir.")
        else:
            st.caption("Listo. Si quedaron páginas SIN_REMITO, reintentá con OCR activado o ajustá el patrón.")

st.caption(
    f"Nota: OCR por API: {'Sí' if (usar_ocr and has_key) else 'No'} • "
    f"Límite OCR ejecución: {MAX_OCR} • Pausa: {SLEEP_BETWEEN_OCR}s • "
    f"Cupo horario usado: {st.session_state.ocr_calls}/{RATE_LIMIT_MAX}"
)
