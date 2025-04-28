from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Dict, Set, Optional, Any
import os
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
import io
import json
import os
import requests
from pydantic import BaseModel
from google.cloud import vision
import uvicorn

# Models for request/response
class InventoryItem(BaseModel):
    name: str

class InventoryResponse(BaseModel):
    inventory: List[str]

class CocktailSummary(BaseModel):
    id: str
    name: str
    can_make: bool
    missing: List[str] = []

class CocktailsResponse(BaseModel):
    makeable: List[CocktailSummary]
    need_more: List[CocktailSummary]

class CocktailDetail(BaseModel):
    id: str
    name: str
    instructions: str
    glass: str
    ingredients: Dict[str, str]
    image_url: str
    can_make: bool
    missing: List[str] = []

class SpiritRequest(BaseModel):
    spirit: str

class BoozeBuddy:
    def __init__(self):
        # Cocktail API setup
        self.api_base_url = "https://www.thecocktaildb.com/api/json/v1/1/"
        self.api_key = "1"  # Free tier key
        
        # Vision API client
        self.vision_client = None
        try:
            self.vision_client = vision.ImageAnnotatorClient()
            print("Google Cloud Vision API client initialized successfully")
        except Exception as e:
            print(f"Failed to initialize Vision API client: {e}")
            print("Image recognition features will be disabled")
        
        # Known alcohol brands and mappings to spirit types
        self.alcohol_mappings = {
            # Whiskey/Bourbon/Scotch
            "jack daniel": "whiskey", "jameson": "whiskey", "johnnie walker": "scotch",
            "maker's mark": "bourbon", "crown royal": "whiskey", "woodford reserve": "bourbon",
            "bulleit": "bourbon", "wild turkey": "bourbon", "glenfiddich": "scotch",
            "macallan": "scotch", "buffalo trace": "bourbon", "bushmills": "whiskey",
            "jim beam": "bourbon", "knob creek": "bourbon", "fireball": "whiskey",
            "chivas": "scotch", "glenlivet": "scotch", "highland park": "scotch",
            
            # Vodka
            "smirnoff": "vodka", "absolut": "vodka", "grey goose": "vodka",
            "ketel one": "vodka", "belvedere": "vodka", "tito": "vodka",
            "stolichnaya": "vodka", "ciroc": "vodka", "skyy": "vodka",
            "finlandia": "vodka", "new amsterdam": "vodka",
            
            # Gin
            "beefeater": "gin", "tanqueray": "gin", "bombay sapphire": "gin",
            "hendrick": "gin", "gordon": "gin", "plymouth": "gin",
            "seagram": "gin", "broker": "gin", "botanist": "gin",
            
            # Rum
            "bacardi": "rum", "captain morgan": "rum", "malibu": "rum",
            "appleton": "rum", "mount gay": "rum", "kraken": "rum",
            "sailor jerry": "rum", "diplomatico": "rum", "havana club": "rum",
            
            # Tequila
            "jose cuervo": "tequila", "patron": "tequila", "don julio": "tequila",
            "casamigos": "tequila", "herradura": "tequila", "1800": "tequila",
            "el jimador": "tequila", "milagro": "tequila", "olmeca": "tequila",
            
            # Liqueurs & Others
            "baileys": "irish cream", "kahlua": "coffee liqueur", "cointreau": "triple sec",
            "grand marnier": "orange liqueur", "disaronno": "amaretto", "southern comfort": "liqueur",
            "jagermeister": "herbal liqueur", "campari": "bitter liqueur", "aperol": "aperitif",
            "martini": "vermouth", "st germain": "elderflower liqueur",
            
            # Generic terms
            "vodka": "vodka", "gin": "gin", "rum": "rum", "tequila": "tequila",
            "whiskey": "whiskey", "whisky": "whiskey", "bourbon": "bourbon",
            "scotch": "scotch", "brandy": "brandy", "cognac": "cognac",
            "vermouth": "vermouth", "triple sec": "triple sec"
        }
        
        # User's inventory - in a real app, this would be stored in a database per user
        self.inventory: Set[str] = set()
        
        # Cache for API calls to reduce repeated requests
        self.api_cache = {}
    
    def detect_labels_in_image(self, image_content):
        """Detect labels in the image using Google Cloud Vision API."""
        if not self.vision_client:
            return []
        
        try:
            image = vision.Image(content=image_content)
            response = self.vision_client.label_detection(image=image)
            labels = response.label_annotations
            
            # Also detect text to read bottle labels
            text_response = self.vision_client.text_detection(image=image)
            texts = text_response.text_annotations
            
            # Extract labels and text
            detected_labels = [label.description.lower() for label in labels]
            detected_text = " ".join([text.description.lower() for text in texts])
            
            # Look for alcohol-related terms in detected content
            detected_alcohol = []
            
            # Check labels
            for label in detected_labels:
                if label in ["alcohol", "liquor", "spirit", "beverage", "bottle", "drink", "wine"]:
                    print(f"Potential alcohol-related item detected: {label}")
                
                # Check if any brand name is in the label
                for brand, spirit_type in self.alcohol_mappings.items():
                    if brand in label:
                        detected_alcohol.append((brand, spirit_type))
            
            # Check text content
            for brand, spirit_type in self.alcohol_mappings.items():
                if brand in detected_text:
                    detected_alcohol.append((brand, spirit_type))
            
            return list(set(detected_alcohol))  # Remove duplicates
            
        except Exception as e:
            print(f"Error detecting labels: {e}")
            return []
    
    def api_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make a request to TheCocktailDB API with caching."""
        # Create a cache key from the endpoint and parameters
        cache_key = endpoint + json.dumps(params) if params else endpoint
        
        # Check if we have this result cached
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
        
        # Make the API request
        url = self.api_base_url + endpoint
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise exception for 4XX/5XX responses
            data = response.json()
            
            # Cache the result
            self.api_cache[cache_key] = data
            return data
        except requests.exceptions.RequestException as e:
            print(f"API request failed: {e}")
            return {"drinks": None}
    
    def search_cocktails_by_ingredient(self, ingredient: str) -> List[Dict]:
        """Search for cocktails that use a specific ingredient."""
        data = self.api_request("filter.php", {"i": ingredient})
        if not data or not data.get("drinks"):
            return []
        return data["drinks"]
    
    def get_cocktail_details(self, cocktail_id: str) -> Optional[Dict]:
        """Get detailed information about a specific cocktail."""
        data = self.api_request("lookup.php", {"i": cocktail_id})
        if not data or not data.get("drinks"):
            return None
        return data["drinks"][0]
    
    def get_available_cocktails(self) -> Dict[str, List[CocktailSummary]]:
        """Find cocktails that can be made with the current inventory."""
        if not self.inventory:
            return {"makeable": [], "need_more": []}
            
        available_cocktails = {"makeable": [], "need_more": []}
        
        # For each ingredient in inventory, find cocktails
        for ingredient in self.inventory:
            cocktails = self.search_cocktails_by_ingredient(ingredient)
            
            for cocktail in cocktails:
                cocktail_id = cocktail["idDrink"]
                
                # Skip if we've already processed this cocktail
                if any(c.id == cocktail_id for c in available_cocktails["makeable"]) or \
                   any(c.id == cocktail_id for c in available_cocktails["need_more"]):
                    continue
                
                # Get full details including all ingredients
                details = self.get_cocktail_details(cocktail_id)
                if not details:
                    continue
                
                # Extract all ingredients
                ingredients = {}
                for i in range(1, 16):  # API supports up to 15 ingredients
                    ing_key = f"strIngredient{i}"
                    meas_key = f"strMeasure{i}"
                    
                    if details.get(ing_key):
                        ingredient_name = details[ing_key].lower()
                        measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
                        ingredients[ingredient_name] = measure
                
                # Check if we have all ingredients
                missing = []
                for ingredient_name in ingredients.keys():
                    if ingredient_name.lower() not in self.inventory:
                        missing.append(ingredient_name)
                
                # Create cocktail summary
                cocktail_summary = CocktailSummary(
                    id=cocktail_id,
                    name=details["strDrink"],
                    can_make=len(missing) == 0,
                    missing=missing
                )
                
                # Add to appropriate list
                if len(missing) == 0:
                    available_cocktails["makeable"].append(cocktail_summary)
                else:
                    available_cocktails["need_more"].append(cocktail_summary)
                
        return available_cocktails
    
    def find_cocktails_by_spirit(self, spirit: str) -> Dict[str, List[CocktailSummary]]:
        """Find cocktails that use a specific spirit."""
        cocktails = self.search_cocktails_by_ingredient(spirit)
        
        spirit_cocktails = {"makeable": [], "need_more": []}
        for cocktail in cocktails:
            cocktail_id = cocktail["idDrink"]
            
            # Get full details including all ingredients
            details = self.get_cocktail_details(cocktail_id)
            if not details:
                continue
            
            # Extract all ingredients
            ingredients = {}
            for i in range(1, 16):  # API supports up to 15 ingredients
                ing_key = f"strIngredient{i}"
                meas_key = f"strMeasure{i}"
                
                if details.get(ing_key):
                    ingredient_name = details[ing_key].lower()
                    measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
                    ingredients[ingredient_name] = measure
            
            # Check if we have all ingredients
            missing = []
            for ingredient_name in ingredients.keys():
                if ingredient_name.lower() not in self.inventory:
                    missing.append(ingredient_name)
            
            # Create cocktail summary
            cocktail_summary = CocktailSummary(
                id=cocktail_id,
                name=details["strDrink"],
                can_make=len(missing) == 0,
                missing=missing
            )
            
            # Add to appropriate list
            if len(missing) == 0:
                spirit_cocktails["makeable"].append(cocktail_summary)
            else:
                spirit_cocktails["need_more"].append(cocktail_summary)
                
        return spirit_cocktails

# Initialize FastAPI
app = FastAPI(
    title="Booze Buddy API",
    description="API for managing bar inventory and finding cocktail recipes",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bedell-up.github.io/Booze-Buddy/"],  # Replace with your actual GitHub Pages URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("No DATABASE_URL environment variable found")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define SQLAlchemy models
class InventoryItem(Base):
    __tablename__ = "inventory_items"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    
    # If you want to add user support later:
    # user_id = Column(Integer, ForeignKey("users.id"))
    # user = relationship("User", back_populates="inventory_items")

# Create tables
Base.metadata.create_all(bind=engine)

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
# Initialize Booze Buddy
booze_buddy = BoozeBuddy()

# Add some sample inventory for testing
sample_items = ["vodka", "rum", "gin", "tequila", "whiskey", "lime juice", "simple syrup"]
for item in sample_items:
    booze_buddy.inventory.add(item)

# API Routes
@app.get("/")
async def root():
    return {"message": "Welcome to Booze Buddy API"}

@app.post("/analyze-image/", response_model=Dict[str, List])
async def analyze_image(file: UploadFile = File(...)):
    """Analyze an image to detect alcohol bottles."""
    try:
        contents = await file.read()
        detected_alcohol = booze_buddy.detect_labels_in_image(contents)
        
        # Format the response
        response = {"detected": []}
        for brand, spirit_type in detected_alcohol:
            response["detected"].append({
                "brand": brand,
                "spirit_type": spirit_type
            })
        
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get inventory
@app.get("/inventory/", response_model=InventoryResponse)
def get_inventory(db: Session = Depends(get_db)):
    items = db.query(InventoryItem).all()
    inventory_items = [item.name for item in items]
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Your bar has {len(inventory_items)} items"
    }

# Add to inventory
@app.post("/inventory/", response_model=InventoryResponse)
def add_to_inventory(item: InventoryItem, db: Session = Depends(get_db)):
    # Check if item already exists
    existing_item = db.query(InventoryItem).filter(InventoryItem.name == item.name.lower()).first()
    if not existing_item:
        db_item = InventoryItem(name=item.name.lower())
        db.add(db_item)
        db.commit()
        db.refresh(db_item)
    
    # Return updated inventory
    items = db.query(InventoryItem).all()
    inventory_items = [item.name for item in items]
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added {item.name} to your inventory"
    }

# Remove from inventory
@app.delete("/inventory/{item_name}", response_model=InventoryResponse)
def remove_from_inventory(item_name: str, db: Session = Depends(get_db)):
    item_name = item_name.lower()
    
    # Find and remove item
    item = db.query(InventoryItem).filter(InventoryItem.name == item_name).first()
    if item:
        db.delete(item)
        db.commit()
        message = f"Removed {item_name} from your inventory"
    else:
        message = f"{item_name} is not in your inventory"
    
    # Return updated inventory
    items = db.query(InventoryItem).all()
    inventory_items = [item.name for item in items]
    
    return {
        "inventory": sorted(inventory_items),
        "message": message
    }

# Add multiple common ingredients
@app.post("/inventory/add-common/", response_model=InventoryResponse)
def add_common_ingredients(items: List[str] = Body(...), db: Session = Depends(get_db)):
    for item_name in items:
        # Check if item already exists
        existing_item = db.query(InventoryItem).filter(InventoryItem.name == item_name.lower()).first()
        if not existing_item:
            db_item = InventoryItem(name=item_name.lower())
            db.add(db_item)
    
    db.commit()
    
    # Return updated inventory
    items = db.query(InventoryItem).all()
    inventory_items = [item.name for item in items]
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added common ingredients to your inventory"
    }

# Initialize demo 
@app.post("/demo/initialize/")
def initialize_demo(db: Session = Depends(get_db)):
    # Clear existing inventory
    db.query(InventoryItem).delete()
    
    # Add demo items
    demo_items = ["vodka", "gin", "rum", "tequila", "triple sec", 
                 "lime juice", "simple syrup", "orange juice", "cranberry juice"]
    
    for item_name in demo_items:
        db_item = InventoryItem(name=item_name.lower())
        db.add(db_item)
    
    db.commit()
    
    # Return updated inventory
    items = db.query(InventoryItem).all()
    inventory_items = [item.name for item in items]
    
    return {
        "message": "Demo initialized with sample inventory",
        "inventory": sorted(inventory_items)
    }

@app.get("/cocktails/available/", response_model=CocktailsResponse)
async def available_cocktails():
    """Get all cocktails that can be made with the current inventory."""
    cocktails = booze_buddy.get_available_cocktails()
    return cocktails

@app.post("/cocktails/by-spirit/", response_model=CocktailsResponse)
async def cocktails_by_spirit(request: SpiritRequest):
    """Find cocktails that use a specific spirit."""
    cocktails = booze_buddy.find_cocktails_by_spirit(request.spirit.lower())
    return cocktails

@app.get("/cocktails/details/{cocktail_id}", response_model=CocktailDetail)
async def cocktail_details(cocktail_id: str):
    """Get detailed information about a specific cocktail."""
    details = booze_buddy.get_cocktail_details(cocktail_id)
    if not details:
        raise HTTPException(status_code=404, detail="Cocktail not found")
    
    # Extract ingredients and measures
    ingredients = {}
    for i in range(1, 16):
        ing_key = f"strIngredient{i}"
        meas_key = f"strMeasure{i}"
        
        if details.get(ing_key):
            ingredient_name = details[ing_key].lower()
            measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
            ingredients[ingredient_name] = measure
    
    # Check missing ingredients
    missing = []
    for ingredient_name in ingredients.keys():
        if ingredient_name not in booze_buddy.inventory:
            missing.append(ingredient_name)
    
    # Create response
    return CocktailDetail(
        id=cocktail_id,
        name=details["strDrink"],
        instructions=details["strInstructions"],
        glass=details["strGlass"],
        ingredients=ingredients,
        image_url=details["strDrinkThumb"],
        can_make=len(missing) == 0,
        missing=missing
    )

@app.post("/cocktails/search/", response_model=List[CocktailSummary])
async def search_cocktails(query: str = Body(..., embed=True)):
    """Search for cocktails by name."""
    data = booze_buddy.api_request("search.php", {"s": query})
    if not data or not data.get("drinks"):
        return []
    
    results = []
    for drink in data["drinks"]:
        # Extract ingredients
        ingredients = []
        for i in range(1, 16):
            ing_key = f"strIngredient{i}"
            if drink.get(ing_key):
                ingredients.append(drink[ing_key].lower())
        
        # Check missing ingredients
        missing = []
        for ingredient in ingredients:
            if ingredient not in booze_buddy.inventory:
                missing.append(ingredient)
        
        # Create cocktail summary
        cocktail_summary = CocktailSummary(
            id=drink["idDrink"],
            name=drink["strDrink"],
            can_make=len(missing) == 0,
            missing=missing
        )
        
        results.append(cocktail_summary)
    
    return results

# Run the application with uvicorn
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)