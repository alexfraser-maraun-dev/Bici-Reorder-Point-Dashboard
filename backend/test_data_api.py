import requests
import json

url = "http://127.0.0.1:8000/api/replenishment/data"
params = {
    "forecast_period": 112,
    "safety_days": 7,
    "growth_multiplier": 1.0
}

try:
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        print("Response Text:", resp.text)
    else:
        data = resp.json()
        print("Status:", data.get("status"))
        print("Keys in data:", data.get("data", {}).keys())
        
        ad_items = data.get("data", {}).get("Bici Adanac", [])
        print("Items in Bici Adanac:", len(ad_items))
        if ad_items:
            print("First item keys:", ad_items[0].keys())
except Exception as e:
    print("Error:", e)
