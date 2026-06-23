from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename


ROOT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT_DIR / "uploads"
DEFAULT_BASE_DIR = Path.home() / "IMSS-BIENESTAR"

app = Flask(__name__, static_folder=str(ROOT_DIR), static_url_path="")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SECRET_KEY"] = "imss-bienestar-dev"

UPLOAD_DIR.mkdir(exist_ok=True)


def normalize_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    dataframe = dataframe.copy()
    dataframe.columns = (
        dataframe.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", "_", regex=True)
    )
    return dataframe


def get_base_dir() -> Path:
    configured = os.environ.get("IMSS_BIENESTAR_BASE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_BASE_DIR


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    base_dir = get_base_dir()
    clues_path = base_dir / "División de Procesamiento de información - Repositorio de Datos" / "CLUES" / "clues.parquet"
    puestos_path = base_dir / "División de Procesamiento de información - Comando Florence Nightingale" / "Proyectos" / "5_Censo" / "Anterior" / "Estados duplicados e inconsistencias" / "Catalogos" / "Catálogo de Código de Puesto CRH.xlsx"

    errors: list[str] = []
    clues_df = pd.DataFrame()
    puestos_df = pd.DataFrame()

    if clues_path.exists():
        try:
            clues_df = normalize_columns(pd.read_parquet(clues_path)).fillna("")
        except Exception as exc:
            errors.append(f"Error al leer CLUES: {exc}")
    else:
        errors.append(f"No se encontró clues.parquet en {clues_path}")

    if puestos_path.exists():
        try:
            puestos_df = normalize_columns(pd.read_excel(puestos_path, skiprows=1)).fillna("")
        except Exception as exc:
            errors.append(f"Error al leer puestos: {exc}")
    else:
        errors.append(f"No se encontró el catálogo de puestos en {puestos_path}")

    for column in ["clues_imb", "nombre_de_la_unidad", "entidad", "municipio"]:
        if column not in clues_df.columns:
            clues_df[column] = ""

    for column in ["descripcion_de_puesto", "nivel"]:
        if column not in puestos_df.columns:
            puestos_df[column] = ""

    return clues_df, puestos_df, errors


clues_df, puestos_df, data_errors = load_data()


def validar_curp(curp: str) -> dict[str, object]:
    if not curp or len(curp) != 18:
        return {"valido": False, "mensaje": "La CURP debe tener 18 caracteres"}

    curp = curp.upper()
    patron = r"^[A-Z]{4}[0-9]{6}[A-Z]{6}[0-9]{2}$"
    if not re.match(patron, curp):
        return {"valido": False, "mensaje": "Formato de CURP inválido. Ejemplo: GODE561231HDFRRL09"}

    try:
        caracteres = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
        valores = {char: index for index, char in enumerate(caracteres)}
        suma = 0
        for index, char in enumerate(curp[:17]):
            suma += valores.get(char, 0) * (18 - index)

        digito_esperado = (10 - (suma % 10)) % 10
        digito_obtenido = int(curp[17]) if curp[17].isdigit() else -1
        if digito_obtenido != digito_esperado:
            return {"valido": False, "mensaje": "Dígito verificador incorrecto"}
    except Exception as exc:
        return {"valido": False, "mensaje": f"Error en validación: {exc}"}

    return {"valido": True, "mensaje": "CURP válida"}


def validar_puesto(puesto: str) -> dict[str, object]:
    if not puesto:
        return {"valido": False, "mensaje": "Debe seleccionar un puesto"}

    existe = (puestos_df["descripcion_de_puesto"] == puesto).any()
    return {"valido": bool(existe), "mensaje": "Puesto válido" if existe else "Puesto no encontrado en catálogo"}


def validar_clues(clues: str) -> dict[str, object]:
    if not clues:
        return {"valido": False, "mensaje": "Debe ingresar una CLUES"}

    existe = (clues_df["clues_imb"].astype(str).str.upper() == clues.upper()).any()
    return {"valido": bool(existe), "mensaje": "CLUES válida" if existe else "CLUES no encontrada en catálogo"}


@app.get("/")
def root() -> object:
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/api/health")
def health() -> object:
    return jsonify(
        {
            "ok": True,
            "base_dir": str(get_base_dir()),
            "clues_rows": int(len(clues_df)),
            "puestos_rows": int(len(puestos_df)),
            "errors": data_errors,
        }
    )


@app.get("/api/search_clues")
def search_clues() -> object:
    query = request.args.get("q", "").strip()
    if len(query) < 2 or clues_df.empty:
        return jsonify([])

    resultados = clues_df[
        clues_df["clues_imb"].astype(str).str.contains(query, case=False, na=False)
        | clues_df["nombre_de_la_unidad"].astype(str).str.contains(query, case=False, na=False)
    ].head(20)
    return jsonify(resultados[["clues_imb", "nombre_de_la_unidad", "entidad", "municipio"]].to_dict("records"))


@app.get("/api/get_clues_data")
def get_clues_data() -> object:
    clues_id = request.args.get("clues", "").upper()
    if not clues_id or clues_df.empty:
        return jsonify({})

    resultado = clues_df[clues_df["clues_imb"].astype(str).str.upper() == clues_id]
    if resultado.empty:
        return jsonify({})

    row = resultado.iloc[0]
    return jsonify(
        {
            "nombre": row.get("nombre_de_la_unidad", ""),
            "entidad": row.get("entidad", ""),
            "municipio": row.get("municipio", ""),
        }
    )


@app.get("/api/get_puestos")
def get_puestos() -> object:
    if puestos_df.empty:
        return jsonify([])

    puestos_limpios = puestos_df.drop_duplicates(subset=["descripcion_de_puesto"])
    return jsonify(puestos_limpios[["descripcion_de_puesto", "nivel"]].to_dict("records"))


@app.post("/api/validate_curp")
def validate_curp() -> object:
    data = request.get_json(silent=True) or {}
    return jsonify(validar_curp(str(data.get("curp", ""))))


@app.post("/api/validate_puesto")
def validate_puesto() -> object:
    data = request.get_json(silent=True) or {}
    return jsonify(validar_puesto(str(data.get("puesto", ""))))


@app.post("/api/validate_clues")
def validate_clues() -> object:
    data = request.get_json(silent=True) or {}
    return jsonify(validar_clues(str(data.get("clues", ""))))


@app.post("/api/upload_document")
def upload_document() -> object:
    if "file" not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Solo se permiten archivos PDF"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({"error": "El archivo excede el límite de 5MB"}), 400

    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_name = f"{timestamp}_{filename}"
    file.save(UPLOAD_DIR / saved_name)
    return jsonify({"success": True, "filename": filename, "saved_name": saved_name, "message": "Archivo subido correctamente"})


@app.post("/api/save_candidate")
def save_candidate() -> object:
    data = request.get_json(silent=True) or {}

    curp_validacion = validar_curp(str(data.get("curp", "")))
    if not curp_validacion["valido"]:
        return jsonify({"success": False, "error": curp_validacion["mensaje"]}), 400

    candidates_dir = ROOT_DIR / "candidatos"
    candidates_dir.mkdir(exist_ok=True)
    candidate_id = datetime.now().strftime("%Y%m%d%H%M%S")
    file_path = candidates_dir / f"candidato_{candidate_id}.json"
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)

    return jsonify({"success": True, "candidato_id": candidate_id, "message": "Candidato guardado correctamente"})


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str) -> object:
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/<path:filename>")
def static_files(filename: str) -> object:
    return send_from_directory(ROOT_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)