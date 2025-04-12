from fastapi import FastAPI
from app.api_routes import router

app = FastAPI(
    title="MixBuddy API",
    description="AI Cocktail Suggestion App",
    version="1.0"
)

app.include_router(router)
