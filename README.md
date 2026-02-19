
# Clasificador de Remitos - App Web (Streamlit)

## Descripción
Subís un PDF con remitos (una página por remito) y la app:
- Separa cada página
- Detecta el número de remito
- Renombra con formato `0000-00000000`
- Ordena
- Devuelve un ZIP con la carpeta **"Remitos Clasificados"**

## Archivos
- `app.py`: app principal de Streamlit
- `requirements.txt`: dependencias (Streamlit + PyPDF2)

## Deploy en Streamlit Cloud
1. Subí `app.py` y `requirements.txt` a un repositorio en GitHub
2. Andá a https://share.streamlit.io (iniciá sesión)
3. "New app" → conectá tu repo → elegí rama y archivo `app.py`
4. Deploy

## Notas
- Si el PDF está escaneado (imágenes sin texto), hacé OCR antes.
- Podés ajustar el patrón regex en la caja de texto para casos particulares.
