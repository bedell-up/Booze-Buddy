import os
import json
import tempfile
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from fastapi import (
    FastAPI, Depends, HTTPException, status, File, UploadFile, Body
)
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

from jose import JWTError, jwt
from passlib.context import CryptContext

# === Google Vision Setup ===
vision_available = False
try:
    from google.cloud import vision

    if 'GOOGLE_APPLICATION_CREDENTIALS_JSON' in os.environ:
        credentials_json = os.environ['GOOGLE_APPLICATION_CREDENTIALS_JSON']
        fd, temp_credentials_file = tempfile.mkstemp()
        with open(temp_credentials_file, 'w') as f:
            f.write(credentials_json)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = temp_credentials_file

    vision_client = vision.ImageAnnotatorClient()
    vision_available = True
except ImportError:
    print("Google Cloud Vision not available.")

# === App Setup ===
app = FastAPI(
    title="Booze Buddy API",
    description="API for managing bar inventory and cocktails",
    version="2.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# === Database Setup ===
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./boozebuddy.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === Models ===
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    inventory_items = relationship("InventoryItem", back_populates="user")

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User", back_populates="inventory_items")

Base.metadata.create_all(bind=engine)

# === Auth Setup ===
SECRET_KEY = os.environ.get("SECRET_KEY", "temporarysecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def authenticate_user(db: Session, username: str, password: str):
    user = get_user(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = get_user(db, username)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# === Pydantic Schemas ===
class Token(BaseModel):
    access_token: str
    token_type: str

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool

class Config:
    from_attributes = True

class InventoryItemCreate(BaseModel):
    name: str

class InventoryResponse(BaseModel):
    inventory: List[str]
    message: str

class CocktailIngredient(BaseModel):
    name: str
    measure: Optional[str]

class CocktailDetails(BaseModel):
    id: str
    name: str
    instructions: str
    image_url: str
    glass: str
    ingredients: List[CocktailIngredient]
    can_make: bool
    missing: List[str]


logger.error("Failed DB commit: %s", str(e))

try:
    db.add(new_user)
    db.commit()
except Exception as e:
    logger.exception("Database error while adding user")
    db.rollback()
    raise HTTPException(status_code=500, detail="Internal server error")

# === BoozeBuddy Cocktail Logic ===
class BoozeBuddy:
    def __init__(self):
        self.api_base = "https://www.thecocktaildb.com/api/json/v2/961249867/"
        self.cache = {}

    def api_request(self, endpoint: str, params: Dict[str, str] = None):
        cache_key = f"{endpoint}-{json.dumps(params)}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        try:
            response = requests.get(self.api_base + endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.cache[cache_key] = data
            return data
        except requests.RequestException:
            return {}

    def get_available_cocktails(self, inventory: List[str]):
        cocktails = {}
        for ingredient in inventory:
            data = self.api_request("filter.php", {"i": ingredient})
            for drink in data.get("drinks", []):
                cocktails[drink["idDrink"]] = drink["strDrink"]
        return cocktails

    def get_cocktail_details(self, cocktail_id: str):
        data = self.api_request("lookup.php", {"i": cocktail_id})
        return data.get("drinks", [])[0] if data.get("drinks") else None

boozebuddy = BoozeBuddy()

# === Routes ===
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow()}

@app.post("/register", response_model=UserResponse)
def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter((User.username == user.username) | (User.email == user.email)).first():
        raise HTTPException(status_code=400, detail="Username or email already registered")
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=get_password_hash(user.password)
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@app.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token({"sub": user.username}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {"access_token": token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/", response_class=HTMLResponse)
def serve_index():
    return FileResponse("static/index.html")

@app.get("/login", response_class=HTMLResponse)
def serve_login():
    return FileResponse("static/login.html")

@app.get("/app", response_class=HTMLResponse)
def serve_app():
    return FileResponse("static/app.html")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.ico")

@app.get("/inventory/", response_model=InventoryResponse)
def get_inventory(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(InventoryItem).filter(InventoryItem.user_id == current_user.id).all()
    return {"inventory": [item.name for item in items], "message": f"{len(items)} items found"}

@app.post("/inventory/", response_model=InventoryResponse)
def add_inventory(item: InventoryItemCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(InventoryItem).filter(InventoryItem.name == item.name, InventoryItem.user_id == current_user.id).first():
        db.add(InventoryItem(name=item.name, user_id=current_user.id))
        db.commit()
    items = db.query(InventoryItem).filter(InventoryItem.user_id == current_user.id).all()
    return {"inventory": [i.name for i in items], "message": f"Added {item.name}"}

@app.delete("/inventory/{item_name}", response_model=InventoryResponse)
def delete_inventory(item_name: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.name == item_name, InventoryItem.user_id == current_user.id).first()
    if item:
        db.delete(item)
        db.commit()
    items = db.query(InventoryItem).filter(InventoryItem.user_id == current_user.id).all()
    return {"inventory": [i.name for i in items], "message": f"Removed {item_name}"}

@app.get("/cocktails/available/", response_model=List[str])
def available_cocktails(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inventory = [item.name for item in db.query(InventoryItem).filter(InventoryItem.user_id == current_user.id).all()]
    cocktails = boozebuddy.get_available_cocktails(inventory)
    return list(cocktails.values())

@app.get("/cocktails/details/{cocktail_id}", response_model=CocktailDetails)
def cocktail_details(cocktail_id: str):
    details = boozebuddy.get_cocktail_details(cocktail_id)
    if not details:
        raise HTTPException(status_code=404, detail="Cocktail not found")
    ingredients = []
    for i in range(1, 16):
        name = details.get(f"strIngredient{i}")
        measure = details.get(f"strMeasure{i}")
        if name:
            ingredients.append(CocktailIngredient(name=name, measure=measure))
    return CocktailDetails(
        id=cocktail_id,
        name=details["strDrink"],
        instructions=details["strInstructions"],
        image_url=details["strDrinkThumb"],
        glass=details["strGlass"],
        ingredients=ingredients,
        can_make=False,
        missing=[]
    )
