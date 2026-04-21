import requests
import os
from dotenv import load_dotenv
load_dotenv()

def exchange_code():
    print("--- Exchanging One-Time Code for Refresh Token ---")
    
    # We use the 'refresh_token' field in .env temporarily as the 'code'
    code = os.getenv("LIGHTSPEED_REFRESH_TOKEN")
    client_id = os.getenv("LIGHTSPEED_CLIENT_ID")
    client_secret = os.getenv("LIGHTSPEED_CLIENT_SECRET")
    
    url = "https://cloud.lightspeedapp.com/oauth/access_token.php"
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code"
    }
    
    response = requests.post(url, data=payload)
    
    if response.status_code == 200:
        data = response.json()
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        
        print("\nSUCCESS!")
        print(f"Access Token: {access_token[:10]}...")
        print(f"Refresh Token: {refresh_token}")
        print("\nUPDATING .env FILE...")
        
        # Read the .env file and replace the refresh token
        with open('.env', 'r') as f:
            lines = f.readlines()
            
        with open('.env', 'w') as f:
            for line in lines:
                if line.startswith("LIGHTSPEED_REFRESH_TOKEN"):
                    f.write(f'LIGHTSPEED_REFRESH_TOKEN="{refresh_token}"\n')
                else:
                    f.write(line)
                    
        print(".env updated with permanent refresh token.")
    else:
        print(f"\nFAILED to exchange code: {response.text}")

if __name__ == "__main__":
    exchange_code()
