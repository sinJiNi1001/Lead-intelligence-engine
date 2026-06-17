import sqlite3
import os
DB_FILE = "lead_intelligence.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lie_engine.db") # New name

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS leads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
      sales_person TEXT,
      company_name TEXT,
      website TEXT,
      service_type TEXT,
      full_input_json TEXT,
      full_output_json TEXT,
      decision TEXT,
      suggested_price TEXT
    )
    ''')
    conn.commit()
    conn.close()
    print("✅ SQLite Database initialized.")


if __name__ == "__main__":
    init_db()