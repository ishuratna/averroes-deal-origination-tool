# ══════════════════════════════════════════════════════════════
# Averroes Deal Origination — Sourcing Filter Configuration
# ══════════════════════════════════════════════════════════════

# 🟢 RELAXED INGESTION CRITERIA
# Update these to change what enters the "Master Universe"
SOURCING_CRITERIA = {
    "regions": ["UK", "Ireland", "United Kingdom", "Great Britain", "London", "Dublin"],
    
    # Financial Scale (in GBP Millions)
    "revenue_min": 1.0,
    "revenue_max": 20.0, # Slightly higher than 10 to catch "near misses"
    
    # Preferred Business Models
    "sectors": [
        "SaaS", "B2B SaaS", "Enterprise Software", "Cloud Computing",
        "FinTech", "HealthTech", "Cybersecurity", "CleanTech", 
        "Tech-Enabled Services", "E-commerce Infrastructure"
    ],
    
    # Minimum Match Score to even consider (0.0 means accept all scraped data)
    "min_ingestion_score": 0.3
}

# 📋 FILTERING CATEGORIES (For Frontend UI)
# These define the groupings in your Dashboard filters
UI_FILTER_OPTIONS = {
    "verticals": [
        "FinTech", "HealthTech", "Cybersecurity", "Artificial Intelligence",
        "E-commerce", "SaaS", "Logistics Tech", "EdTech", "Industrial Tech"
    ],
    "regions": ["UK", "Ireland", "Mainland Europe", "North America"],
    "deal_status": ["Scraped", "Enriched", "AI Analyzed", "Contacted", "In Pipeline"]
}
