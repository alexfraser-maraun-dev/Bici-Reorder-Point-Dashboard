from google.oauth2.credentials import Credentials
import os

creds = Credentials(
    token=None,
    refresh_token="test",
    token_uri="https://oauth2.googleapis.com/token",
    client_id="test",
    client_secret="test",
    scopes=["test"]
)

print(f"valid: {creds.valid}")
print(f"expired: {creds.expired}")
print(f"refresh_token: {creds.refresh_token}")
