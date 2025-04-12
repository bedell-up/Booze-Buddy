import requests

API_URL = "https://www.thecocktaildb.com/api/json/v1/1"

def suggest_drinks(primary_alcohol):
    res = requests.get(f"{API_URL}/filter.php?i={primary_alcohol}")
    return res.json()["drinks"]

def fetch_recipe_details(drink_id):
    res = requests.get(f"{API_URL}/lookup.php?i={drink_id}")
    return res.json()["drinks"][0]
