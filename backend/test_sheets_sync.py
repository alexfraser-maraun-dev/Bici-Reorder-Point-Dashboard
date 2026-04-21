import os
import json
from dotenv import load_dotenv
load_dotenv()

from app.services import google_sheets
import traceback

def test_sync():
    print("--- Starting BICI Calculation Sync Test ---")
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    
    try:
        print(f"Fetching data from: {spreadsheet_id}...")
        data = google_sheets.fetch_sheet_data(spreadsheet_id)
        print(f"Successfully fetched {len(data)} rows.")
        
        print("\nRunning BICI Recommendation Calculations...")
        recommendations = google_sheets.process_recommendations(data, safety_days=7)
        print(f"Generated {len(recommendations)} location-specific recommendations.")
        
        # Filter for rows that actually need a change
        changes = [r for r in recommendations if r['change_needed']]
        print(f"Items requiring an update in Lightspeed: {len(changes)}")
        
        if changes:
            print("\nSample Recommendation Requiring Update:")
            print(json.dumps(changes[0], indent=2))
            
            # Print a few more to see diversity
            if len(changes) > 1:
                print("\nAnother sample:")
                print(json.dumps(changes[1], indent=2))
                
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_sync()
