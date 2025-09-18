# migrate_add_columns.py
import sqlite3
import shutil
import os

DB = 'ventas.db'
TABLE = 'venta'  # si tu tabla tiene otro nombre, cambia esto

if not os.path.exists(DB):
    print(f"No se encontró '{DB}' en el directorio actual: {os.getcwd()}")
    raise SystemExit(1)

# Hacer backup por seguridad
bak = DB + '.bak'
shutil.copyfile(DB, bak)
print(f"Copia de seguridad creada: {bak}")

conn = sqlite3.connect(DB)
cur = conn.cursor()

def has_column(table, column):
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [r[1] for r in cur.fetchall()]
    return column in cols

try:
    if not has_column(TABLE, 'pagado_fecha'):
        cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN pagado_fecha TEXT")
        print("Añadida columna: pagado_fecha")
    else:
        print("pagado_fecha ya existe")

    if not has_column(TABLE, 'enviado_fecha'):
        cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN enviado_fecha TEXT")
        print("Añadida columna: enviado_fecha")
    else:
        print("enviado_fecha ya existe")

    conn.commit()
    print("Migración completada con éxito.")
finally:
    conn.close()
