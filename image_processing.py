import os
from google.cloud import vision
from PIL import Image
import io

async def process_image(file):
    content = await file.read()
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=content)
    response = client.label_detection(image=image)
    
    labels = [label.description for label in response.label_annotations]
    detected_bottles = [label for label in labels if label in known_alcohols()]
    
    return detected_bottles

def known_alcohols():
    return ["Vodka", "Gin", "Rum", "Whiskey", "Tequila", "Brandy"]
