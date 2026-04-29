import os
from py_clob_client.client import ClobClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv('keys.env')

def main():
    host = "https://clob.polymarket.com"
    key = os.getenv("PK")  # Private key without '0x'
    chain_id = 137  # Polygon Mainnet

    if not key:
        raise ValueError("Private key not found. Please set PK in environment variables.")

    # Initialize client
    client = ClobClient(host, key=key, chain_id=chain_id)

    # Generate API credentials
    try:
        api_creds = client.create_or_derive_api_creds()
        print("API Key:", api_creds.api_key)
        print("Secret:", api_creds.api_secret)
        print("Passphrase:", api_creds.api_passphrase)
        # Save these credentials securely for future use
    except Exception as e:
        print("Error creating credentials:", e)

if __name__ == "__main__":
    main()   