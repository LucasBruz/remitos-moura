
import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import re
import os
from pathlib import Path
import shutil

st.set_page_config(page_title="Clasificador de Remitos", page_icon="📦", layout="centered")
st.title("📦 Clasificador de Remitos - App Web")
st.write("Subí un PDF y la app separa, reconoce, ordena y renombra los remitos en un ZIP listo para descargar.")

uploaded_pdf = st.file_uploader("📄 Subir PDF", type=["pdf"])
patron = st.text_input("🔍 Patrón (regex) para detectar remitos", value=r"\b\d{4}-\d{8}\b")
procesar = st.button("🚀 Procesar PDF")


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


def detectar(texto: str, patron_rx: str):
    if patron_rx:
        try:
            m = re.search(patron_rx, texto)
            if m:
                g = m.group(1) if m.groups() else m.group(0)
                return normalizar_remito(g)
        except Exception:
            pass
    # Heurística adicional sin guion (10-14 dígitos)
    m2 = re.search(r"\b(\d{10,14})\b", texto)
    if m2:
        return normalizar_remito(m2.group(1))
    return None


if procesar and uploaded_pdf:
    with st.spinner("Procesando PDF…"):
        tmp_dir = Path("remitos_tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()

        clasificados = tmp_dir / "Remitos Clasificados"
        clasificados.mkdir()

        reader = PdfReader(uploaded_pdf)
        registros = []

        for i, page in enumerate(reader.pages):
            writer = PdfWriter()
            writer.add_page(page)

            texto = page.extract_text() or ""
            remito = detectar(texto, patron)

            if remito:
                nombre = f"{remito}.pdf"
                registros.append((remito, nombre))
            else:
                nombre = f"SIN_REMITO_{i+1}.pdf"

            with open(clasificados / nombre, "wb") as f:
                writer.write(f)

        registros.sort(key=lambda x: tuple(map(int, x[0].split('-'))))

        for idx, (rem, archivo) in enumerate(registros, 1):
            ori = clasificados / archivo
            nuevo = clasificados / f"{idx:06d}_{archivo}"
            ori.rename(nuevo)

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

st.caption("Nota: si tu PDF es escaneado (solo imágenes), necesitás OCR previo para extraer el número de remito.")
