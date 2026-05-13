import os
from sqlalchemy import text
from app.db.database import engine
from dotenv import load_dotenv

load_dotenv()

def test_db_connection():
    print("--- Testing Database Connection ---")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found in environment. Using default (likely SQLite).")
    else:
        # Mask password for printing
        masked_url = db_url
        if "@" in db_url:
            parts = db_url.split("@")
            creds = parts[0].split(":")
            if len(creds) > 2:
                masked_url = f"{creds[0]}:{creds[1]}:****@{parts[1]}"
        print(f"Connecting to: {masked_url}")

    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print(f"Connection successful! Result: {result.fetchone()[0]}")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    test_db_connection()
