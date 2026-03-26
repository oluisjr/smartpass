import sqlite3
import os

DB_FILE = "smartpass.db"  # ajuste se o seu arquivo tiver outro nome

if not os.path.exists(DB_FILE):
    raise SystemExit(f"Arquivo não encontrado: {DB_FILE}")

conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()

# tenta adicionar a coluna
try:
    cur.execute("ALTER TABLE access_grants ADD COLUMN created_by TEXT;")
    conn.commit()
    print("OK: coluna created_by adicionada.")
except sqlite3.OperationalError as e:
    print("Aviso:", e)

conn.close()
