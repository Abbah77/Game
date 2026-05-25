from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import dotenv
import random

dotenv.load_dotenv()

# ============================================
# DATA MODELS
# ============================================

class ColonyCreate(BaseModel):
    player_name: str
    colony_name: str = "Wild Grove"

class ColonySave(BaseModel):
    resources: Optional[Dict] = None
    animals: Optional[List[Dict]] = None
    crops: Optional[List[Dict]] = None
    buildings: Optional[List[Dict]] = None

class ResourceUpdate(BaseModel):
    wood: int
    stone: int
    food: int
    grove_coins: int

# ============================================
# INITIALIZATION
# ============================================

# Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

# CORS settings
allowed_origins = [
    os.getenv("NETLIFY_URL", "http://localhost:3000"),
    "https://*.netlify.app"
]

app = FastAPI(title="WildGrove API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# API ROUTES
# ============================================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/api/colony")
async def create_or_get_colony(data: ColonyCreate):
    """Get existing colony or create a new one"""
    
    # Check if colony exists
    existing = supabase.table("colonies")\
        .select("*")\
        .eq("player_name", data.player_name)\
        .execute()
    
    if existing.data:
        colony = existing.data[0]
        
        # Get resources
        resources = supabase.table("resources")\
            .select("*")\
            .eq("colony_id", colony["id"])\
            .execute()
        
        # Get animals
        animals = supabase.table("animals")\
            .select("*")\
            .eq("colony_id", colony["id"])\
            .eq("is_alive", True)\
            .execute()
        
        return {
            "colony": colony,
            "resources": resources.data[0] if resources.data else None,
            "animals": animals.data
        }
    
    # Create new colony
    new_colony = supabase.table("colonies").insert({
        "player_name": data.player_name,
        "colony_name": data.colony_name
    }).execute()
    
    colony_id = new_colony.data[0]["id"]
    
    # Create default resources
    supabase.table("resources").insert({
        "colony_id": colony_id,
        "wood": 100,
        "stone": 50,
        "food": 50,
        "grove_coins": 100
    }).execute()
    
    # Create starter animals
    starter_animals = [
        {"colony_id": colony_id, "name": "Bruno", "type": "goat", "role": "Saboteur", 
         "x_position": 400, "y_position": 300, "health": 100, "hunger": 100, "thirst": 100, "happiness": 75},
        {"colony_id": colony_id, "name": "Rex", "type": "dog", "role": "Defender",
         "x_position": 450, "y_position": 300, "health": 100, "hunger": 100, "thirst": 100, "happiness": 75},
        {"colony_id": colony_id, "name": "Porkchop", "type": "pig", "role": "Harvester",
         "x_position": 350, "y_position": 320, "health": 100, "hunger": 100, "thirst": 100, "happiness": 75},
    ]
    
    supabase.table("animals").insert(starter_animals).execute()
    
    return {
        "colony": new_colony.data[0],
        "resources": {"wood": 100, "stone": 50, "food": 50, "grove_coins": 100},
        "animals": starter_animals
    }

@app.post("/api/colony/{colony_id}/save")
async def save_colony(colony_id: str, data: ColonySave):
    """Save full colony state"""
    
    # Update resources
    if data.resources:
        supabase.table("resources")\
            .update({
                "wood": data.resources.get("wood", 0),
                "stone": data.resources.get("stone", 0),
                "food": data.resources.get("food", 0),
                "grove_coins": data.resources.get("groveCoins", 0),
                "updated_at": datetime.now().isoformat()
            })\
            .eq("colony_id", colony_id)\
            .execute()
    
    # Update animals (mark old as dead, insert new)
    if data.animals:
        # Mark existing as dead
        supabase.table("animals")\
            .update({"is_alive": False})\
            .eq("colony_id", colony_id)\
            .execute()
        
        # Insert alive animals
        alive_animals = []
        for animal in data.animals:
            if animal.get("health", 0) > 0:
                alive_animals.append({
                    "colony_id": colony_id,
                    "name": animal.get("name"),
                    "type": animal.get("type"),
                    "role": animal.get("role"),
                    "health": animal.get("health", 100),
                    "hunger": animal.get("hunger", 100),
                    "thirst": animal.get("thirst", 100),
                    "happiness": animal.get("happiness", 75),
                    "x_position": animal.get("x", 400),
                    "y_position": animal.get("y", 300),
                    "is_alive": True
                })
        
        if alive_animals:
            supabase.table("animals").insert(alive_animals).execute()
    
    # Update last_active
    supabase.table("colonies")\
        .update({
            "last_active": datetime.now().isoformat(),
            "last_saved": datetime.now().isoformat()
        })\
        .eq("id", colony_id)\
        .execute()
    
    return {"success": True}

@app.get("/api/colony/{colony_id}/events")
async def get_events(colony_id: str):
    """Get unread colony events (offline attacks)"""
    
    events = supabase.table("colony_events")\
        .select("*")\
        .eq("colony_id", colony_id)\
        .eq("was_read", False)\
        .order("created_at", desc=True)\
        .limit(20)\
        .execute()
    
    # Mark as read
    if events.data:
        event_ids = [e["id"] for e in events.data]
        supabase.table("colony_events")\
            .update({"was_read": True})\
            .in_("id", event_ids)\
            .execute()
    
    return events.data or []

@app.post("/api/colony/{colony_id}/claim-daily")
async def claim_daily(colony_id: str):
    """Claim daily bonus"""
    
    colony = supabase.table("colonies")\
        .select("last_active")\
        .eq("id", colony_id)\
        .execute()
    
    if not colony.data:
        raise HTTPException(status_code=404, detail="Colony not found")
    
    last_active = colony.data[0].get("last_active")
    if last_active:
        last_claim = datetime.fromisoformat(last_active.replace('Z', '+00:00'))
    else:
        last_claim = datetime.min
    
    now = datetime.now()
    hours_since = (now - last_claim).total_seconds() / 3600
    
    if hours_since >= 24:
        supabase.table("resources")\
            .update({"grove_coins": supabase.raw("grove_coins + 50")})\
            .eq("colony_id", colony_id)\
            .execute()
        
        return {"claimed": True, "amount": 50}
    else:
        hours_left = 24 - hours_since
        return {"claimed": False, "hoursLeft": int(hours_left)}

# ============================================
# OFFLINE THREAT ENGINE
# ============================================

def process_offline_threats():
    """Check all colonies for offline damage"""
    print(f"🛡️ Running offline threat check at {datetime.now()}")
    
    colonies = supabase.table("colonies")\
        .select("*")\
        .eq("status", "active")\
        .execute()
    
    for colony in colonies.data or []:
        last_active = colony.get("last_active")
        if not last_active:
            continue
        
        # Parse last_active time
        if isinstance(last_active, str):
            last_active = datetime.fromisoformat(last_active.replace('Z', '+00:00'))
        
        hours_offline = (datetime.now() - last_active).total_seconds() / 3600
        
        # Only process if offline between 30 mins and 48 hours
        if 0.5 < hours_offline < 48:
            damage = {}
            threat_level = "low"
            
            if hours_offline < 2:
                threat_level = "low"
                damage = {"food": random.randint(5, 15)}
            elif hours_offline < 6:
                threat_level = "medium"
                damage = {"wood": random.randint(10, 30)}
            elif hours_offline < 12:
                threat_level = "high"
                damage = {"stone": random.randint(10, 25)}
            else:
                threat_level = "critical"
                damage = {"grove_coins": random.randint(20, 50)}
            
            # Apply damage
            for resource, amount in damage.items():
                supabase.table("resources")\
                    .update({resource: supabase.raw(f"{resource} - {amount}")})\
                    .eq("colony_id", colony["id"])\
                    .execute()
            
            # Log event
            supabase.table("colony_events").insert({
                "colony_id": colony["id"],
                "event_type": "offline_attack",
                "threat_level": threat_level,
                "damage": damage
            }).execute()
            
            print(f"⚠️ Attack on {colony['player_name']}: {threat_level}")

# Start background scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(process_offline_threats, 'interval', minutes=15)
scheduler.start()

# ============================================
# START SERVER
# ============================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)
