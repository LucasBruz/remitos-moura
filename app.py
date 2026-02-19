import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import re
import os
from pathlib import Path
import shutil
import io
import requests
import time

st.set_page_config(page_title="Clasificador de Remitos", page_icon="📦", layout="centered")
st.title("📦 Clasificador de Remitos - App Web (con OCR por API)")
st.write("Subí un PDF; la app separa, reconoce (texto directo o por OCR en la nube), ordena y renombra los remitos, y devuelve un ZIP.")

uploaded_pdf = st.file_uploader("📄 Subir PDF", type=["pdf"])
patron = st.text_input("🔍 Patrón (regex) para detectar remitos", value=r"\b\d{4}-\d{8}\b")
procesar = st.button("🚀 Procesar PDF")

# ===== Utils =====
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
    m2 = re.search(r"\b(\d{10,14})\b", texto)
    if m2:
        norm = normalizar_remito(m2.group(1))
        if norm:
            return norm
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
    Envía un PDF (1 página) a un OCR en la nube y devuelve el texto plano.
    Usa OCR.space como ejemplo (requiere API key).
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
    r = requests.post(url, files=files, data=data, headers=headers, timeout=120)
    r.raise_for_status()
    js = r.json()
    if js.get("IsErroredOnProcessing"):
        raise RuntimeError(js.get("ErrorMessage") or "OCR error")
    results = js.get("ParsedResults", [])
    if not results:
        return ""
    return results[0].get("ParsedText", "") or ""

# ===== Main =====
if procesar and uploaded_pdf:
    # Tomamos la API key desde los secrets (Streamlit Cloud > Settings > Secrets)
    api_key = st.secrets.get("OCRSPACE_API_KEY", None)

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
        registros = []

        for i in range(total):
            # 1) Intento texto directo
            texto = extraer_texto_por_pypdf2(reader, i)
            remito = detectar_por_texto(texto, patron)

            # 2) Si no detecta y hay API key, intento OCR por API
            if not remito and api_key:
                # Genero un PDF de 1 página en memoria
                buf = io.BytesIO()
                w = PdfWriter()
                w.add_page(reader.pages[i])
                w.write(buf)
                buf.seek(0)

                try:
                    texto_ocr = ocr_api_pdf_bytes(buf.read(), api_key, language="spa")
                    remito = detectar_por_texto(texto_ocr, patron)
                    # Pequeña pausa para evitar rate-limit en planes gratuitos
                    time.sleep(1.0)
                except Exception as e:
                    st.warning(f"⚠️ OCR falló en la página {i+1}: {e}")

            # 3) Guardar la página individual siempre
            writer = PdfWriter()
            writer.add_page(reader.pages[i])

            if remito:
                nombre = f"{remito}.pdf"
                registros.append((remito, nombre))
            else:
                nombre = f"SIN_REMITO_{i+1}.pdf"

            with open(clasificados / nombre, "wb") as f:
                writer.write(f)

        # Orden por sucursal y número
        registros.sort(key=lambda x: tuple(map(int, x[0].split('-'))))

        # Prefijo para mantener orden en ZIP
        for idx, (rem, archivo) in enumerate(registros, 1):
            ori = clasificados / archivo
            nuevo = clasificados / f"{idx:06d}_{archivo}"
            if ori.exists():
                ori.rename(nuevo)

        # Armar ZIP
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

nota = "Nota: si no configurás la API key, la app igualmente funciona con texto embebido; el OCR en la nube se activará sólo si hay API key."
st.caption(nota)
