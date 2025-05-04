import os
import json
import tempfile
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import httpx
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, status, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from passlib.context import CryptContext
from jose import jwt, JWTError
from pydantic import BaseModel

# ---------------------- CONFIG ----------------------

app = FastAPI(
    title="Booze Buddy API",
    description="Optimized API for managing bar inventory and discovering cocktails",
    version="2.0.0"
)

# Static files
if not os.path.exists('static'):
    raise RuntimeError("Missing 'static' directory")

app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS
origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost,https://www.boozebuddy.online").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./boozebuddy.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------- MODELS ----------------------

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    inventory_items = relationship("InventoryItem", back_populates="user")

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("User", back_populates="inventory_items")

Base.metadata.create_all(bind=engine)

# ---------------------- AUTH ----------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
SECRET_KEY = os.getenv("SECRET_KEY", "temporarysecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def authenticate_user(db: Session, username: str, password: str):
    user = get_user(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

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
    except JWTError:
        raise credentials_exception
    user = get_user(db, username=username)
    if user is None:
        raise credentials_exception
    return user

# ---------------------- SCHEMAS ----------------------

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool

class Token(BaseModel):
    access_token: str
    token_type: str

class InventoryItemCreate(BaseModel):
    name: str

class InventoryResponse(BaseModel):
    inventory: List[str]
    message: str

# ---------------------- HELPERS ----------------------

def get_current_inventory(db: Session, user_id: int) -> List[str]:
    items = db.query(InventoryItem).filter(InventoryItem.user_id == user_id).all()
    return [item.name for item in items]

# ---------------------- ROUTES ----------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return FileResponse("static/index.html")

@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    return FileResponse("static/login.html")

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = User(
        username=user.username,
        email=user.email,
        hashed_password=get_password_hash(user.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user.username}, 
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/inventory/", response_model=InventoryResponse)
def get_inventory(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inventory_items = get_current_inventory(db, current_user.id)
    return {"inventory": sorted(inventory_items), "message": f"{len(inventory_items)} items in inventory"}

@app.post("/inventory/", response_model=InventoryResponse)
def add_to_inventory(item: InventoryItemCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(InventoryItem).filter(InventoryItem.name == item.name.lower(), InventoryItem.user_id == current_user.id).first():
        db.add(InventoryItem(name=item.name.lower(), user_id=current_user.id))
        db.commit()
    inventory_items = get_current_inventory(db, current_user.id)
    return {"inventory": sorted(inventory_items), "message": f"Added {item.name} to inventory"}

@app.delete("/inventory/{item_name}", response_model=InventoryResponse)
def remove_from_inventory(item_name: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.name == item_name.lower(), InventoryItem.user_id == current_user.id).first()
    if item:
        db.delete(item)
        db.commit()
        message = f"Removed {item_name}"
    else:
        message = f"{item_name} not found"
    inventory_items = get_current_inventory(db, current_user.id)
    return {"inventory": sorted(inventory_items), "message": message}

# ---------------------- EXTERNAL API (Optimized) ----------------------

async def fetch_cocktail_data(endpoint: str, params: Dict = None) -> Dict:
    async with httpx.AsyncClient(timeout=10) as client:
        url = f"https://www.thecocktaildb.com/api/json/v2/961249867/{endpoint}"
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            print(f"Error fetching data: {e}")
            return {}

# ---------------------- RUN ----------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
