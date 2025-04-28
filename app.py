import os
import io
import time
import requests
import json
import base64
from typing import List, Dict, Set, Optional, Any
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Body, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from PIL import Image

vision_available = False
try:
    from google.cloud import vision
    vision_available = True
except ImportError:
    print("Google Cloud Vision not available. Image recognition features will be disabled.")

# Define vision_available before trying to import
vision_available = False

# Set up Google Cloud credentials from environment variable if available
if 'GOOGLE_APPLICATION_CREDENTIALS_JSON' in os.environ:
    try:
        # Get the JSON content from environment variable
        credentials_json = os.environ['GOOGLE_APPLICATION_CREDENTIALS_JSON']
        
        # Create a temporary file to store credentials
        fd, temp_credentials_file = tempfile.mkstemp()
        with open(temp_credentials_file, 'w') as f:
            f.write(credentials_json)
        
        # Set the environment variable to the temporary file path
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = temp_credentials_file
        print("Successfully set up Google Cloud credentials from environment variable")
        
        # Now try to import vision after credentials are set up
        try:
            from google.cloud import vision
            vision_available = True
            print("Google Cloud Vision API imported successfully")
        except ImportError:
            print("Failed to import Google Cloud Vision API")
    except Exception as e:
        print(f"Error setting up Google Cloud credentials: {e}")
else:
    print("No Google Cloud credentials found in environment variables")
    try:
        from google.cloud import vision
        vision_available = True
        print("Google Cloud Vision API imported successfully (using default credentials)")
    except ImportError:
        print("Failed to import Google Cloud Vision API")

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Fallback for local development
    DATABASE_URL = "sqlite:///./boozebuddy.db"
    print(f"No DATABASE_URL found, using SQLite: {DATABASE_URL}")

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

# Create FastAPI app
app = FastAPI(
    title="Booze Buddy API",
    description="API for managing your bar inventory and discovering cocktails",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins like your GitHub Pages URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models for request/response
class InventoryItemCreate(BaseModel):
    name: str

class InventoryResponse(BaseModel):
    inventory: List[str]
    message: str

class CocktailIngredient(BaseModel):
    name: str
    measure: Optional[str] = None

class CocktailDetails(BaseModel):
    id: str
    name: str
    instructions: str
    image_url: str
    glass: str
    ingredients: List[CocktailIngredient]
    can_make: bool
    missing: List[str] = []

class CocktailList(BaseModel):
    cocktails: List[CocktailDetails]
    can_make_count: int
    total_count: int
    
class ImageAnalysisResult(BaseModel):
    detections: List[Dict[str, str]]
    message: str

class SearchQuery(BaseModel):
    query: str
    
# BoozeBuddy class for cocktail data and image detection
class BoozeBuddy:
    def __init__(self):
        # Cocktail API setup
        self.api_base_url = "https://www.thecocktaildb.com/api/json/v1/1/"
        self.api_key = "1"  # Free tier key
        
        # Vision API client
        self.vision_client = None
        if vision_available:
            try:
                self.vision_client = vision.ImageAnnotatorClient()
            except Exception as e:
                print(f"Failed to initialize Vision API client: {e}")
        
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
    
    def get_available_cocktails(self, db_inventory: List[str]) -> Dict[str, Dict]:
        """Find cocktails that can be made with the current inventory."""
        if not db_inventory:
            return {}
                
        available_cocktails = {}
        
        # For each ingredient in inventory, find cocktails
        for ingredient in db_inventory:
            cocktails = self.search_cocktails_by_ingredient(ingredient)
            
            for cocktail in cocktails:
                cocktail_id = cocktail["idDrink"]
                
                # Skip if we've already processed this cocktail
                if cocktail_id in available_cocktails:
                    continue
                
                # Get full details including all ingredients
                details = self.get_cocktail_details(cocktail_id)
                if not details:
                    continue
                
                # Extract all ingredients and measures
                ingredients = {}
                for i in range(1, 16):  # API supports up to 15 ingredients
                    ing_key = f"strIngredient{i}"
                    meas_key = f"strMeasure{i}"
                    
                    if details.get(ing_key):
                        ingredient_name = details[ing_key].lower()
                        measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
                        ingredients[ingredient_name] = measure
                
                # Check if we have all ingredients
                missing = set()
                for ingredient_name in ingredients.keys():
                    if ingredient_name.lower() not in db_inventory:
                        missing.add(ingredient_name)
                
                # Store the cocktail with its missing ingredients
                available_cocktails[cocktail_id] = {
                    "name": details["strDrink"],
                    "image": details["strDrinkThumb"],
                    "instructions": details["strInstructions"],
                    "glass": details["strGlass"],
                    "ingredients": ingredients,
                    "missing": missing,
                    "can_make": len(missing) == 0
                }
                    
        return available_cocktails
    
    def find_cocktails_by_spirit(self, spirit: str, db_inventory: List[str]) -> Dict[str, Dict]:
        """Find cocktails that use a specific spirit."""
        cocktails = self.search_cocktails_by_ingredient(spirit)
        
        spirit_cocktails = {}
        for cocktail in cocktails:
            cocktail_id = cocktail["idDrink"]
            
            # Get full details including all ingredients
            details = self.get_cocktail_details(cocktail_id)
            if not details:
                continue
            
            # Extract all ingredients and measures
            ingredients = {}
            for i in range(1, 16):  # API supports up to 15 ingredients
                ing_key = f"strIngredient{i}"
                meas_key = f"strMeasure{i}"
                
                if details.get(ing_key):
                    ingredient_name = details[ing_key].lower()
                    measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
                    ingredients[ingredient_name] = measure
            
            # Check if we have all ingredients
            missing = set()
            for ingredient_name in ingredients.keys():
                if ingredient_name.lower() not in db_inventory:
                    missing.add(ingredient_name)
            
            # Store the cocktail with its missing ingredients
            spirit_cocktails[cocktail_id] = {
                "name": details["strDrink"],
                "image": details["strDrinkThumb"],
                "instructions": details["strInstructions"],
                "glass": details["strGlass"],
                "ingredients": ingredients,
                "missing": missing,
                "can_make": len(missing) == 0
            }
                
        return spirit_cocktails
    
    def search_cocktails_by_name(self, name: str, db_inventory: List[str]) -> Dict[str, Dict]:
        """Search for cocktails by name."""
        data = self.api_request("search.php", {"s": name})
        if not data or not data.get("drinks"):
            return {}
                
        results = {}
        for drink in data["drinks"]:
            cocktail_id = drink["idDrink"]
            
            # Extract all ingredients and measures
            ingredients = {}
            for i in range(1, 16):  # API supports up to 15 ingredients
                ing_key = f"strIngredient{i}"
                meas_key = f"strMeasure{i}"
                
                if drink.get(ing_key):
                    ingredient_name = drink[ing_key].lower()
                    measure = drink.get(meas_key, "").strip() if drink.get(meas_key) else ""
                    ingredients[ingredient_name] = measure
            
            # Check if we have all ingredients
            missing = set()
            for ingredient_name in ingredients.keys():
                if ingredient_name.lower() not in db_inventory:
                    missing.add(ingredient_name)
            
            # Store the cocktail with its missing ingredients
            results[cocktail_id] = {
                "name": drink["strDrink"],
                "image": drink["strDrinkThumb"],
                "instructions": drink["strInstructions"],
                "glass": drink["strGlass"],
                "ingredients": ingredients,
                "missing": missing,
                "can_make": len(missing) == 0
            }
                
        return results

# Create a single instance of BoozeBuddy to be used by all API endpoints
boozebuddy = BoozeBuddy()

# Common mixers and ingredients for demo purposes
common_ingredients = [
    "lemon juice", "lime juice", "orange juice", "cranberry juice", "pineapple juice",
    "simple syrup", "grenadine", "bitters", "soda water", "tonic water", "cola",
    "ginger beer", "cream", "milk", "coffee liqueur", "sugar"
]

# Helper function to get current inventory from database
def get_current_inventory(db: Session):
    items = db.query(InventoryItem).all()
    return [item.name for item in items]

# API Endpoints
@app.get("/")
def read_root():
    return {"message": "Welcome to Booze Buddy API! See /docs for API documentation."}

@app.post("/analyze-image/", response_model=ImageAnalysisResult)
async def analyze_image(file: UploadFile = File(...)):
    """
    Analyze an image to detect alcohol bottles.
    The image is processed using Google Cloud Vision API.
    """
    if not vision_available:
        raise HTTPException(status_code=501, detail="Vision API not available")
    
    content = await file.read()
    detections = boozebuddy.detect_labels_in_image(content)
    
    return {
        "detections": [{"brand": brand, "type": spirit_type} for brand, spirit_type in detections],
        "message": f"Detected {len(detections)} potential alcohol items"
    }

@app.get("/inventory/", response_model=InventoryResponse)
def get_inventory(db: Session = Depends(get_db)):
    """Get the current bar inventory."""
    inventory_items = get_current_inventory(db)
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Your bar has {len(inventory_items)} items"
    }

@app.post("/inventory/", response_model=InventoryResponse)
def add_to_inventory(item: InventoryItemCreate, db: Session = Depends(get_db)):
    """Add an item to the bar inventory."""
    # Check if item already exists
    existing_item = db.query(InventoryItem).filter(InventoryItem.name == item.name.lower()).first()
    if not existing_item:
        db_item = InventoryItem(name=item.name.lower())
        db.add(db_item)
        db.commit()
    
    inventory_items = get_current_inventory(db)
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added {item.name} to your inventory"
    }

@app.delete("/inventory/{item_name}", response_model=InventoryResponse)
def remove_from_inventory(item_name: str, db: Session = Depends(get_db)):
    """Remove an item from the bar inventory."""
    item_name = item_name.lower()
    
    # Find and remove item
    item = db.query(InventoryItem).filter(InventoryItem.name == item_name).first()
    if item:
        db.delete(item)
        db.commit()
        message = f"Removed {item_name} from your inventory"
    else:
        message = f"{item_name} is not in your inventory"
    
    inventory_items = get_current_inventory(db)
    return {
        "inventory": sorted(inventory_items),
        "message": message
    }

@app.post("/inventory/add-common/", response_model=InventoryResponse)
def add_common_ingredients(items: List[str] = Body(...), db: Session = Depends(get_db)):
    """Add multiple common ingredients to the inventory."""
    for item_name in items:
        # Check if item already exists
        existing_item = db.query(InventoryItem).filter(InventoryItem.name == item_name.lower()).first()
        if not existing_item:
            db_item = InventoryItem(name=item_name.lower())
            db.add(db_item)
    
    db.commit()
    
    inventory_items = get_current_inventory(db)
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added common ingredients to your inventory"
    }

@app.get("/ingredients/common/", response_model=List[str])
def get_common_ingredients():
    """Get a list of common cocktail ingredients."""
    return common_ingredients

@app.get("/cocktails/available/", response_model=CocktailList)
def get_available_cocktails(db: Session = Depends(get_db)):
    """Get cocktails that can be made with the current inventory."""
    inventory_items = get_current_inventory(db)
    cocktails = boozebuddy.get_available_cocktails(inventory_items)
    
    # Convert to response format
    result = []
    for id, info in cocktails.items():
        ingredients = [
            {"name": name, "measure": measure} 
            for name, measure in info["ingredients"].items()
        ]
        
        result.append({
            "id": id,
            "name": info["name"],
            "instructions": info["instructions"],
            "image_url": info["image"],
            "glass": info.get("glass", "Glass"),
            "ingredients": ingredients,
            "can_make": info["can_make"],
            "missing": list(info["missing"])
        })
    
    # Sort: first cocktails that can be made, then by name
    result.sort(key=lambda x: (not x["can_make"], x["name"]))
    
    return {
        "cocktails": result,
        "can_make_count": sum(1 for c in result if c["can_make"]),
        "total_count": len(result)
    }

@app.get("/cocktails/by-spirit/{spirit}", response_model=CocktailList)
def get_cocktails_by_spirit(spirit: str, db: Session = Depends(get_db)):
    """Get cocktails that use a specific spirit."""
    inventory_items = get_current_inventory(db)
    cocktails = boozebuddy.find_cocktails_by_spirit(spirit.lower(), inventory_items)
    
    # Convert to response format
    result = []
    for id, info in cocktails.items():
        ingredients = [
            {"name": name, "measure": measure} 
            for name, measure in info["ingredients"].items()
        ]
        
        result.append({
            "id": id,
            "name": info["name"],
            "instructions": info["instructions"],
            "image_url": info["image"],
            "glass": info.get("glass", "Glass"),
            "ingredients": ingredients,
            "can_make": info["can_make"],
            "missing": list(info["missing"])
        })
    
    # Sort: first cocktails that can be made, then by name
    result.sort(key=lambda x: (not x["can_make"], x["name"]))
    
    return {
        "cocktails": result,
        "can_make_count": sum(1 for c in result if c["can_make"]),
        "total_count": len(result)
    }

@app.get("/cocktails/details/{cocktail_id}", response_model=CocktailDetails)
def get_cocktail_details(cocktail_id: str, db: Session = Depends(get_db)):
    """Get detailed information about a specific cocktail."""
    inventory_items = get_current_inventory(db)
    details = boozebuddy.get_cocktail_details(cocktail_id)
    
    if not details:
        raise HTTPException(status_code=404, detail=f"Cocktail with ID {cocktail_id} not found")
    
    # Extract ingredients and measures
    ingredients = []
    missing = []
    
    for i in range(1, 16):
        ing_key = f"strIngredient{i}"
        meas_key = f"strMeasure{i}"
        
        if details.get(ing_key):
            ingredient_name = details[ing_key].lower()
            measure = details.get(meas_key, "").strip() if details.get(meas_key) else ""
            
            ingredients.append({
                "name": ingredient_name,
                "measure": measure
            })
            
            # Check if ingredient is in inventory
            if ingredient_name not in inventory_items:
                missing.append(ingredient_name)
    
    return {
        "id": cocktail_id,
        "name": details["strDrink"],
        "instructions": details["strInstructions"],
        "image_url": details["strDrinkThumb"],
        "glass": details["strGlass"],
        "ingredients": ingredients,
        "can_make": len(missing) == 0,
        "missing": missing
    }

@app.post("/cocktails/search/", response_model=CocktailList)
def search_cocktails(query: SearchQuery, db: Session = Depends(get_db)):
    """Search for cocktails by name."""
    inventory_items = get_current_inventory(db)
    cocktails = boozebuddy.search_cocktails_by_name(query.query, inventory_items)
    
    # Convert to response format
    result = []
    for id, info in cocktails.items():
        ingredients = [
            {"name": name, "measure": measure} 
            for name, measure in info["ingredients"].items()
        ]
        
        result.append({
            "id": id,
            "name": info["name"],
            "instructions": info["instructions"],
            "image_url": info["image"],
            "glass": info.get("glass", "Glass"),
            "ingredients": ingredients,
            "can_make": info["can_make"],
            "missing": list(info["missing"])
        })
    
    # Sort: first cocktails that can be made, then by name
    result.sort(key=lambda x: (not x["can_make"], x["name"]))
    
    return {
        "cocktails": result,
        "can_make_count": sum(1 for c in result if c["can_make"]),
        "total_count": len(result)
    }

# For demo purposes, add some items to the inventory
@app.post("/demo/initialize/")
def initialize_demo(db: Session = Depends(get_db)):
    """Initialize demo data with some inventory items for testing."""
    # Clear existing inventory
    db.query(InventoryItem).delete()
    
    # Add demo items
    demo_items = ["vodka", "gin", "rum", "tequila", "triple sec", 
                 "lime juice", "simple syrup", "orange juice", "cranberry juice"]
    
    for item_name in demo_items:
        db_item = InventoryItem(name=item_name.lower())
        db.add(db_item)
    
    db.commit()
    
    inventory_items = get_current_inventory(db)
    return {
        "message": "Demo initialized with sample inventory",
        "inventory": sorted(inventory_items)
    }

# Run the application with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)