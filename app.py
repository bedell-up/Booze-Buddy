import os
import io
import time
import requests
import json
import base64
import tempfile
from typing import List, Dict, Set, Optional, Any
from datetime import datetime, timedelta

# FastAPI imports
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Body, Query, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

# Keep only one app definition at the top
app = FastAPI(
    title="Booze Buddy API",
    description="API for managing your bar inventory and discovering cocktails",
    version="1.0.0"
)

# Mount the static directory for access via /static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Also mount static files at the root to enable direct access to style.css
app.mount("/", StaticFiles(directory="static"), name="root")

# Serve index.html at root
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("static/index.html")

# Root route to serve the main application
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("static/index.html")

# Login page
@app.get("/login", response_class=HTMLResponse)
async def read_login():
    return FileResponse("static/login.html")

# App page
@app.get("/app", response_class=HTMLResponse)
async def read_app():
    return FileResponse("static/app.html")
# Pydantic imports
from pydantic import BaseModel, EmailStr

# SQLAlchemy imports
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# Authentication imports
from passlib.context import CryptContext
from jose import JWTError, jwt

# Image processing
from PIL import Image

# Google Cloud Vision (optional)
vision_available = False
try:
    from google.cloud import vision
    vision_available = True
except ImportError:
    print("Google Cloud Vision not available. Image recognition features will be disabled.")

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

# Define the get_db dependency function
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# SQLAlchemy models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    
    # Relationship to inventory items
    inventory_items = relationship("InventoryItem", back_populates="user")

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    
    # Add user relationship
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable for backward compatibility
    user = relationship("User", back_populates="inventory_items")

# Create tables
Base.metadata.create_all(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Secret key for JWT - generate a secure random key
SECRET_KEY = os.environ.get("SECRET_KEY", "temporarysecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Pydantic models for authentication
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool

# Password functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# User functions
def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def authenticate_user(db: Session, username: str, password: str):
    user = get_user(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

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

# Create FastAPI app
app = FastAPI(
    title="Booze Buddy API",
    description="API for managing your bar inventory and discovering cocktails",
    version="1.0.0"
)

# Try using a more direct approach for specific pages
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html", "r") as f:
        return f.read()

@app.get("/login", response_class=HTMLResponse)
async def read_login():
    with open("static/login.html", "r") as f:
        return f.read()

# Root route to serve the main application
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("static/index.html")

# Login page
@app.get("/login", response_class=HTMLResponse)
async def read_login():
    return FileResponse("static/login.html")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.boozebuddy.online"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BoozeBuddy class for cocktail data and image detection
class BoozeBuddy:
    def __init__(self):
        # Cocktail API setup - UPDATED for premium access
        self.api_base_url = "https://www.thecocktaildb.com/api/json/v2/961249867/"
        
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
def get_current_inventory(db: Session, user_id: Optional[int] = None):
    if user_id:
        items = db.query(InventoryItem).filter(InventoryItem.user_id == user_id).all()
    else:
        items = db.query(InventoryItem).all()
    return [item.name for item in items]

# API Endpoints
@app.get("/health")
def health_check():
    """Check if the API is running."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "Booze Buddy API"
    }

# Authentication endpoints
@app.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    # Check if username exists
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Check if email exists
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

# Inventory endpoints
@app.get("/inventory/", response_model=InventoryResponse)
def get_inventory(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get the current user's bar inventory."""
    inventory_items = get_current_inventory(db, current_user.id)
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Your bar has {len(inventory_items)} items"
    }

@app.post("/inventory/", response_model=InventoryResponse)
def add_to_inventory(
    item: InventoryItemCreate, 
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add an item to the user's bar inventory."""
    # Check if item already exists for this user
    existing_item = db.query(InventoryItem).filter(
        InventoryItem.name == item.name.lower(),
        InventoryItem.user_id == current_user.id
    ).first()
    
    if not existing_item:
        db_item = InventoryItem(name=item.name.lower(), user_id=current_user.id)
        db.add(db_item)
        db.commit()
    
    # Get updated inventory
    inventory_items = get_current_inventory(db, current_user.id)
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added {item.name} to your inventory"
    }

@app.delete("/inventory/{item_name}", response_model=InventoryResponse)
def remove_from_inventory(
    item_name: str, 
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove an item from the user's bar inventory."""
    item_name = item_name.lower()
    
    # Find and remove item - make sure it belongs to the current user
    item = db.query(InventoryItem).filter(
        InventoryItem.name == item_name,
        InventoryItem.user_id == current_user.id
    ).first()
    
    if item:
        db.delete(item)
        db.commit()
        message = f"Removed {item_name} from your inventory"
    else:
        message = f"{item_name} is not in your inventory"
    
    # Get updated inventory
    inventory_items = get_current_inventory(db, current_user.id)
    
    return {
        "inventory": sorted(inventory_items),
        "message": message
    }

@app.post("/inventory/add-common/", response_model=InventoryResponse)
def add_common_ingredients(
    items: List[str] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add multiple common ingredients to the user's inventory."""
    for item_name in items:
        # Check if item already exists for this user
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.name == item_name.lower(),
            InventoryItem.user_id == current_user.id
        ).first()
        
        if not existing_item:
            db_item = InventoryItem(name=item_name.lower(), user_id=current_user.id)
            db.add(db_item)
    
    db.commit()
    
    # Get updated inventory
    inventory_items = get_current_inventory(db, current_user.id)
    
    return {
        "inventory": sorted(inventory_items),
        "message": f"Added common ingredients to your inventory"
    }

@app.get("/ingredients/common/", response_model=List[str])
def get_common_ingredients():
    """Get a list of common cocktail ingredients."""
    return common_ingredients

# Image analysis endpoint
@app.post("/analyze-image/", response_model=ImageAnalysisResult)
async def analyze_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
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

# Cocktail endpoints
@app.get("/cocktails/available/", response_model=CocktailList)
def get_available_cocktails(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get cocktails that can be made with the current user's inventory."""
    # Get user's inventory
    inventory_items = get_current_inventory(db, current_user.id)
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
def get_cocktails_by_spirit(
    spirit: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get cocktails that use a specific spirit."""
    # Get user's inventory
    inventory_items = get_current_inventory(db, current_user.id)
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
def get_cocktail_details(
    cocktail_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific cocktail."""
    # Get user's inventory
    inventory_items = get_current_inventory(db, current_user.id)
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
def search_cocktails(
    query: SearchQuery,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Search for cocktails by name."""
    # Get user's inventory
    inventory_items = get_current_inventory(db, current_user.id)
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
def initialize_demo(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Initialize demo data with some inventory items for testing."""
    # Remove existing inventory for this user
    db.query(InventoryItem).filter(InventoryItem.user_id == current_user.id).delete()
    
    # Add demo items
    demo_items = ["vodka", "gin", "rum", "tequila", "triple sec", 
                 "lime juice", "simple syrup", "orange juice", "cranberry juice"]
    
    for item_name in demo_items:
        db_item = InventoryItem(name=item_name.lower(), user_id=current_user.id)
        db.add(db_item)
    
    db.commit()
    
    # Get updated inventory
    inventory_items = get_current_inventory(db, current_user.id)
    
    return {
        "message": "Demo initialized with sample inventory",
        "inventory": sorted(inventory_items)
    }

@app.get("/debug-files")
def debug_files():
    import os
    import glob
    
    # Get all files in the current directory and subdirectories
    all_files = glob.glob("**/*", recursive=True)
    
    # Check specifically for static directory
    static_exists = os.path.exists("static")
    static_files = []
    if static_exists:
        static_files = os.listdir("static")
    
    # Get the current working directory
    cwd = os.getcwd()
    
    return {
        "current_directory": cwd,
        "all_files": all_files,
        "static_exists": static_exists,
        "static_files": static_files
    }
@app.get("/login", response_class=HTMLResponse)
async def read_login():
    import os
    print(f"Current directory: {os.getcwd()}")
    print(f"Static dir exists: {os.path.exists('static')}")
    if os.path.exists('static/login.html'):
        print("Found login.html in static directory")
    elif os.path.exists('login.html'):
        print("Found login.html in root directory")
    else:
        print("login.html not found")
        
# Run the application with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
