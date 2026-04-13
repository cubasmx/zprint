import os
import json
import socket
import csv
import pymysql
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import xmlrpc.client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuración de Odoo
ODOO_URL = os.getenv('ODOO_URL', 'https://tuercasyabrazaderasensa.odoo.com')
ODOO_DB = os.getenv('ODOO_DB', 'tuercasyabrazaderasensa')
ODOO_USERNAME = os.getenv('ODOO_USERNAME', '')
ODOO_PASSWORD = os.getenv('ODOO_PASSWORD', '')

class OdooClientCache:
    def __init__(self):
        self.common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
        self.models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
        try:
            self.uid = self.common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
        except Exception as e:
            self.uid = None
            print("Odoo Auth Error:", e)

    def search_products(self, query):
        if not self.uid:
            return []
        domain = ['|', ('default_code', 'ilike', query), ('name', 'ilike', query)]
        fields = ['id', 'name', 'default_code']
        try:
            products = self.models.execute_kw(
                ODOO_DB, self.uid, ODOO_PASSWORD,
                'product.template', 'search_read',
                [domain], {'fields': fields, 'limit': 20}
            )
            return [{'id_producto': p.get('default_code', 'N/A'), 'nombre': p.get('name', 'N/A')} for p in products]
        except Exception as e:
            print("Odoo Search Error:", e)
            return []

odoo_client = None

def get_odoo():
    global odoo_client
    if not odoo_client or not getattr(odoo_client, 'uid', None):
        odoo_client = OdooClientCache()
    return odoo_client

# Models for API
class SearchReq(BaseModel):
    query: str

class PrinterCfg(BaseModel):
    printer_ip: str
    printer_port: int
    vertical_offset_dots: int
    horizontal_offset_dots: int

class PrintReq(BaseModel):
    producto: dict
    op: str
    versionsgc: str
    cantidad: int
    totallote: int
    numinicio: int
    printer_ip: str
    printer_port: int
    vertical_offset_dots: int
    horizontal_offset_dots: int

@app.get("/")
def read_root():
    return FileResponse("main.html")

@app.get("/api/health")
def api_health():
    client = get_odoo()
    return {"odoo_ok": bool(client.uid)}

@app.post("/api/search")
def api_search(req: SearchReq):
    client = get_odoo()
    results = client.search_products(req.query)
    return {"results": results}

CONFIG_FILE = "data/printer_config.json"
HISTORY_FILE = "data/history.csv"

@app.get("/api/printer-config")
def get_printer_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        "printer_ip": "10.10.2.20", "printer_port": 9100,
        "vertical_offset_dots": 0, "horizontal_offset_dots": 0
    }

@app.post("/api/printer-config")
def set_printer_config(cfg: PrinterCfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg.dict(), f)
    return {"message": "Configuración guardada exitosamente"}

@app.get("/api/history")
def get_history():
    if not os.path.exists(HISTORY_FILE):
        return {"history": []}
    history = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)
        # Devolver sólo los últimos 100 registros para UI
        return {"history": history[::-1][:100]}
    except Exception as e:
        return {"history": []}

@app.get("/api/history/export")
def export_history():
    if not os.path.exists(HISTORY_FILE):
        raise HTTPException(status_code=404, detail="No hay historial para exportar")
    return FileResponse(HISTORY_FILE, media_type="text/csv", filename="historial_zprint.csv")

@app.post("/api/print")
def api_print(req: PrintReq):
    try:
        qty = req.cantidad
        id_producto = req.producto.get('id_producto', 'N/A')
        nombre = req.producto.get('nombre', 'N/A')
        op_desc = req.op
        sgc_version = req.versionsgc
        
        # Convert chars to latin-1
        nombre_print = nombre.encode('latin1', errors='replace').decode('latin1')
        op_print = op_desc.encode('latin1', errors='replace').decode('latin1')
        sgc_print = sgc_version.encode('latin1', errors='replace').decode('latin1')
        v_offset = req.vertical_offset_dots
        h_offset = req.horizontal_offset_dots
        
        totallote = req.totallote
        numinicio = req.numinicio
        current_date_str = datetime.now().strftime("%d/%m/%Y")
        current_date_str_exact = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect((req.printer_ip, req.printer_port))
            for i in range(1, qty + 1):
                actual_num = numinicio + i - 1
                zpl_label = f"""^XA
^LH{h_offset},{v_offset}
^FO55,15^A0N,20,20^FD{nombre_print}^FS
^FO100,40^BCN,75,Y,N,N^FD{id_producto}^FS
^FO55,148^A0N,22,22^FD{op_print}^FS
^FO260,148^A0N,20,20^FD{current_date_str}^FS
^FO55,168^A0N,20,20^FD{actual_num}/{totallote}^FS
^FO260,168^A0N,20,20^FD{sgc_print}^FS
^PQ1,1,1,Y^XZ"""
                sock.sendall(zpl_label.encode('latin1'))

        try:
            os.makedirs("data", exist_ok=True)
            file_exists = os.path.exists(HISTORY_FILE)
            with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Fecha y Hora", "ID Producto", "Nombre", "OP", "Versión SGC", "Cantidad", "Total Lote", "Inicio"])
                writer.writerow([current_date_str_exact, id_producto, nombre, op_desc, sgc_version, qty, totallote, numinicio])
        except Exception as e:
            print("No se pudo guardar el CSV:", e)

        # MySQL Backup
        mysql_host = os.getenv("MYSQL_HOST")
        mysql_user = os.getenv("MYSQL_USER")
        mysql_pwd = os.getenv("MYSQL_PASSWORD")
        mysql_db = os.getenv("MYSQL_DB", "zprint")
        mysql_port = int(os.getenv("MYSQL_PORT", "3306"))

        if mysql_host and mysql_user:
            try:
                conn = pymysql.connect(
                    host=mysql_host, port=mysql_port,
                    user=mysql_user, password=mysql_pwd,
                    autocommit=True
                )
                with conn.cursor() as cursor:
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {mysql_db}")
                    cursor.execute(f"USE {mysql_db}")
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS impresiones (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            fecha_hora DATETIME,
                            id_producto VARCHAR(100),
                            nombre VARCHAR(255),
                            op VARCHAR(100),
                            version_sgc VARCHAR(100),
                            cantidad INT,
                            total_lote INT,
                            inicio INT
                        )
                    """)
                    cursor.execute("""
                        INSERT INTO impresiones 
                        (fecha_hora, id_producto, nombre, op, version_sgc, cantidad, total_lote, inicio)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (current_date_str_exact, id_producto, nombre, op_desc, sgc_version, qty, totallote, numinicio))
                conn.close()
            except Exception as e:
                print("No se pudo guardar el backup en MySQL:", e)

        # removed dead mysql history connection
        return {"message": f"{qty} etiquetas enviadas a {req.printer_ip}"}
    except ConnectionRefusedError:
        raise HTTPException(status_code=503, detail=f"Conexión rechazada en {req.printer_ip}:{req.printer_port}")
    except socket.timeout:
        raise HTTPException(status_code=504, detail="Tiempo de espera agotado conectando a la impresora")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
