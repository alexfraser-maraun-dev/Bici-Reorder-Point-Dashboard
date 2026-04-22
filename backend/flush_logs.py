import sqlite3
import os

db_path = "/Users/alesfraser-maraun/Desktop/BICI_replen_level_automation/backend/replen_app.db"

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Count records
tables = ["recommendation_runs", "recommendation_rows", "writeback_logs"]
for table in tables:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"Table '{table}': {count} records")
    except sqlite3.OperationalError as e:
        print(f"Error reading table '{table}': {e}")

# Delete records
for table in tables:
    try:
        cursor.execute(f"DELETE FROM {table}")
        print(f"Deleted records from '{table}'")
    except sqlite3.OperationalError as e:
        print(f"Error deleting from table '{table}': {e}")

conn.commit()
conn.close()
print("Database flush complete.")
