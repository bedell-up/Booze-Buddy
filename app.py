# app.py
from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from passlib.hash import bcrypt
from jose import JWTError, jwt
import os
import uuid
import shutil

# === CONFIG ===
DATABASE_URL = os.environ.get("DATABASE_URL")
SECRET_KEY = os.environ.get("SECRET_KEY", "devsecret")
ALGORITHM = "HS256"

# === DATABASE SETUP ===
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    inventory = relationship("InventoryItem", back_populates="owner")

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="inventory")

Base.metadata.create_all(bind=engine)

# === FASTAPI APP ===
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === DEPENDENCIES ===
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === UTILS ===
def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def verify_password(plain, hashed):
    return bcrypt.verify(plain, hashed)

# === ROUTES ===
@app.post("/register")
def register(username: str, email: str, password: str, db: Session = Depends(get_db)):
    if db.query(User).filter((User.username == username) | (User.email == email)).first():
        raise HTTPException(status_code=400, detail="Username or email already registered")
    user = User(username=username, email=email, password_hash=bcrypt.hash(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": "User registered"}

@app.post("/token")
def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/users/me")
def get_me(token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {"username": user.username, "email": user.email}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/inventory/")
def get_inventory(token: str, db: Session = Depends(get_db)):
    user = get_me(token, db)
    items = db.query(InventoryItem).filter(InventoryItem.user_id == user["id"]).all()
    return {"inventory": [item.name for item in items]}

@app.post("/inventory/")
def add_inventory(name: str, token: str, db: Session = Depends(get_db)):
    user = get_me(token, db)
    item = InventoryItem(name=name, user_id=user["id"])
    db.add(item)
    db.commit()
    return {"message": "Item added"}

@app.delete("/inventory/{item_id}")
def delete_inventory(item_id: int, token: str, db: Session = Depends(get_db)):
    user = get_me(token, db)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.user_id == user["id"]).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"message": "Item deleted"}
