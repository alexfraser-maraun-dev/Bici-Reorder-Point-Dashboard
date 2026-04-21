import os
import json
from dotenv import load_dotenv
load_dotenv()

from app.services import google_sheets
import traceback

def test_sync():
    print("--- Verifying Token Permissions ---")
    try:
        client = google_sheets.get_gspread_client()
        print("Successfully authenticated.")
        
        # Try to list all spreadsheet titles the user can see
        print("Fetching list of accessible spreadsheets...")
        spreadsheet_list = client.openall()
        print(f"Found {len(spreadsheet_list)} spreadsheets.")
        for s in spreadsheet_list:
            print(f"- {s.title} (ID: {s.id})")
            
        spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
        print(f"\nAttempting to open specific sheet: {spreadsheet_id}")
        spreadsheet = client.open_by_key(spreadsheet_id)
        print(f"Success! Opened: {spreadsheet.title}")
            
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_sync()
