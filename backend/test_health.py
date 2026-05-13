import os
from dotenv import load_dotenv
load_dotenv()
from app.services.lightspeed_client import LightspeedClient

def test_health():
    ls = LightspeedClient()
    print(f"Testing health check to {ls.base_url}.json...")
    is_ok = ls.check_health()
    print(f"Health check result: {is_ok}")

if __name__ == '__main__':
    test_health()
