import requests
import time

BASE_URL = "https://averroes-deal-backend-890361705054.europe-west1.run.app"

def trigger_ingestions():
    print(f"🚀 Rehydrating Master Database at {BASE_URL}")
    
    # 1. Marketplaces
    marketplaces = ["Acquire.com", "Flippa", "Microns", "SideProjectors"]
    for m in marketplaces:
        print(f"\n📦 Ingesting Marketplace: {m}...")
        try:
            r = requests.post(f"{BASE_URL}/ingest/marketplace", params={"marketplace_name": m})
            print(f"   Result: {r.json().get('status')} | New Total: {r.json().get('total_in_universe')}")
        except Exception as e:
            print(f"   Failed: {e}")

    # 2. Ranking Lists
    rankings = ["FT 1000", "Startups 100 UK", "Deloitte Fast 50 UK"]
    for rank in rankings:
        print(f"\n📊 Ingesting Ranking List: {rank}...")
        try:
            r = requests.post(f"{BASE_URL}/ingest/ranking", params={"list_name": rank})
            print(f"   Result: {r.json().get('status')} | New Total: {r.json().get('total_in_universe')}")
        except Exception as e:
            print(f"   Failed: {e}")

    # 3. Conferences
    conferences = ["SaaStock Europe", "London Tech Week"]
    for conf in conferences:
        print(f"\n🎤 Ingesting Conference exhibitors: {conf}...")
        try:
            r = requests.post(f"{BASE_URL}/ingest/conference", params={"conference_name": conf})
            print(f"   Result: {r.json().get('status')} | New Total: {r.json().get('total_in_universe')}")
        except Exception as e:
            print(f"   Failed: {e}")

    print("\n✅ Database Rehydration Complete. Refresh your dashboard!")

if __name__ == "__main__":
    trigger_ingestions()
