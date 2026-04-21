import os
import json
from dotenv import load_dotenv
load_dotenv()

from app.services import google_sheets
import traceback

def test_sync():
    print("--- Verifying Data Structure (Original Order) ---")
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        data = google_sheets.fetch_sheet_data(spreadsheet_id)
        if data:
            print(f"Row 1 Keys: {list(data[0].keys())}")
            print(f"\nRow 1 Values: {list(data[0].values())}")
            
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_sync()
