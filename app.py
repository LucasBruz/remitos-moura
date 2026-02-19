import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import re
import os
from pathlib import Path
import shutil

# OCR y rasterizado
import easyocr
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

st.set_page_config(page_title="Clasificador de Remitos", page_icon="📦", layout="centered")
st.title("📦 Clasificador de Remitos - App Web (con OCR)")
st.write("Subí un PDF; la app separa, reconoce (texto o imagen vía OCR), ordena y renombra los remitos, y devuelve un ZIP.")

uploaded_pdf = st.file_uploader("📄 Subir PDF", type=["pdf"])
patron = st.text_input("🔍 Patrón (regex) para detectar remitos", value=r"\b\d{4}-\d{8}\b")
procesar = st.button("🚀 Procesar PDF")

# ========= Utilities =========

@st.cache_resource
def get_ocr_reader():
    # Idiomas más típicos: español e inglés (agregá otros si te hace falta, ej. 'pt').
    return easyocr.Reader(['es', 'en'], gpu=False)

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

def render_pagina_a_imagen(reader: pdfium.PdfDocument, index: int, scale: float = 2.0) -> Image.Image:
    # Render a imagen con pdfium (sin dependencias del sistema).
    page = reader.get_page(index)
    bitmap = page.render(scale=scale).to_pil()
    page.close()
    return bitmap.convert("RGB")

def ocr_imagen(img: Image.Image) -> str:
    # EasyOCR devuelve lista de [bbox, text, confidence]
    # Unimos todos los textos en un string.
    reader = get_ocr_reader()
    arr = np.array(img)
    results = reader.readtext(arr, detail=1, paragraph=True)
    textos = []
    for _, texto, conf in results:
        if conf is None or conf < 0.2:
            # Filtrar ruido muy bajo
            continue
        textos.append(texto)
    return "\n".join(textos)

def extraer_texto_por_pypdf2(pdfreader: PdfReader, idx: int) -> str:
    try:
        page = pdfreader.pages[idx]
        t = page.extract_text()
        return t or ""
    except Exception:
        return ""

# ========= Flujo principal =========

if procesar and uploaded_pdf:
    with st.spinner("Procesando PDF… (esto puede tardar si hay OCR)"):
        tmp_dir = Path("remitos_tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()

        clasificados = tmp_dir / "Remitos Clasificados"
        clasificados.mkdir()

        reader = PdfReader(uploaded_pdf)
        total_pag = len(reader.pages)
        registros = []

        # Abrimos el PDF con pypdfium2 para rasterizar cuando haga falta
        uploaded_pdf.seek(0)  # reset pointer para pdfium
        pdfium_doc = pdfium.PdfDocument(uploaded_pdf.read())
        # (Volvemos a abrir PyPDF2 después de leer en pdfium)
        uploaded_pdf.seek(0)
        reader = PdfReader(uploaded_pdf)

        for i in range(total_pag):
            # 1) Intento texto directo
            texto = extraer_texto_por_pypdf2(reader, i)
            remito = detectar_por_texto(texto, patron)

            # 2) Si no lo encontré, intento OCR
            if not remito:
                try:
                    img = render_pagina_a_imagen(pdfium_doc, i, scale=2.0)
                    texto_ocr = ocr_imagen(img)
                    remito = detectar_por_texto(texto_ocr, patron)
                except Exception as e:
                    # Si algo falla en OCR, continuamos sin detener todo el proceso
                    st.warning(f"⚠️ OCR falló en la página {i+1}: {e}")

            # 3) Guardar página individual (siempre)
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

        # Prefijo para mantener orden dentro del ZIP
        for idx, (rem, archivo) in enumerate(registros, 1):
            ori = clasificados / archivo
            nuevo = clasificados / f"{idx:06d}_{archivo}"
            if ori.exists():
                ori.rename(nuevo)

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

st.caption("Nota: ahora la app usa OCR automáticamente cuando no encuentra texto (PDFs escaneados).")
