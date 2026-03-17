from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
import numpy as np
import shutil
import os
import json
from pillow_heif import register_heif_opener
register_heif_opener()

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# --- 1. MOSAIC SETUP ---
GRID_WIDTH = 32
GRID_HEIGHT = 44

# Load the target image and calculate the target colors once on startup
TARGET_IMAGE_PATH = "static/gram_portrait.jpg"
target_img = Image.open(TARGET_IMAGE_PATH).convert('RGB').resize((GRID_WIDTH, GRID_HEIGHT))
STATE_FILE = "mosaic_state.json"


def sync_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        valid_state = {}
        for index, data in state.items():
            # Extract the URL from our new dictionary structure
            url = data.get("url")
            filename = url.split("/")[-1]
            local_path = os.path.join(UPLOAD_DIR, filename)

            if os.path.exists(local_path):
                valid_state[index] = data

        with open(STATE_FILE, "w") as f:
            json.dump(valid_state, f)
        return valid_state
    return {}


grid_state = sync_state()

# Build a fast-lookup dictionary to track the current "High Score" of every tile
current_distances = {i: float('inf') for i in range(GRID_WIDTH * GRID_HEIGHT)}
for str_idx, data in grid_state.items():
    current_distances[int(str_idx)] = data.get("distance", float('inf'))

# Reshape the image into a flat list of RGB arrays (length = width * height)
target_colors = np.array(target_img).reshape(-1, 3)


def get_best_tile_index(img_obj):
    # Resizing to 1x1 is a fast trick to get the average RGB of the whole image
    avg_color = np.array(img_obj.resize((1, 1)))[0][0]

    # Calculate Euclidean distance to all target tiles
    distances = np.sqrt(np.sum((target_colors - avg_color) ** 2, axis=1))

    # Return the index of the closest matching tile
    return int(np.argmin(distances))


# --- 2. WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()


# --- 3. ROUTES ---

@app.get("/")
async def get_display():
    with open("static/display.html", "r") as f:
        return HTMLResponse(f.read())


@app.get("/upload")
async def get_upload():
    with open("static/upload.html", "r") as f:
        return HTMLResponse(f.read())


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    temp_location = os.path.join(UPLOAD_DIR, file.filename)
    with open(temp_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    filename_no_ext = os.path.splitext(file.filename)[0]
    updated_tiles = []

    try:
        img = Image.open(temp_location)
        rgb_img = img.convert('RGB')

        # Get the average color of the guest photo
        avg_color = np.array(rgb_img.resize((1, 1)))[0][0]

        # --- UPGRADE 1: Perceptual Color Matching (KEPT) ---
        # Weights for human eye luminance sensitivity (Red, Green, Blue)
        weights = np.array([0.299, 0.587, 0.114])
        distances = np.sqrt(np.sum(weights * (target_colors - avg_color) ** 2, axis=1))

        # Find the indices of the top 10 best matching cells for this photo
        top_10_indices = np.argsort(distances)[:10]

        for i in top_10_indices:
            dist = distances[i]

            # The Competition: Does this new photo beat the current tile's score?
            if dist < current_distances[i]:
                current_distances[i] = dist

                target_rgb = tuple(target_colors[i].astype(int))
                color_overlay = Image.new("RGB", rgb_img.size, target_rgb)

                # --- REVERTED UPGRADE 2: Back to a fixed, reliable alpha ---
                blended_img = Image.blend(rgb_img, color_overlay, alpha=0.6)

                # Save a unique file for this specific tile
                safe_filename = f"{filename_no_ext}_tile_{i}.jpg"
                safe_location = os.path.join(UPLOAD_DIR, safe_filename)
                blended_img.save(safe_location, "JPEG", quality=85)

                image_url = f"/uploads/{safe_filename}"

                # Update the state with both the URL and the winning distance score
                grid_state[str(i)] = {"url": image_url, "distance": float(dist)}
                updated_tiles.append({"index": int(i), "url": image_url})

        # Clean up the original un-tinted file
        if os.path.exists(temp_location):
            os.remove(temp_location)

    except Exception as e:
        print(f"Error processing image: {e}")
        return {"status": "error"}

    # Save the master state to the JSON file
    with open(STATE_FILE, "w") as f:
        json.dump(grid_state, f)

    # Broadcast ALL winning updates to the display screen
    for tile in updated_tiles:
        await manager.broadcast(json.dumps(tile))

    return {"status": "success"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/api/state")
async def get_state():
    # The frontend only expects the URLs, so we flatten the dictionary
    return {k: v["url"] for k, v in sync_state().items()}