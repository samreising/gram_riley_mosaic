import requests
import time
import io

# The URL of your local FastAPI server
UPLOAD_URL = "http://localhost:8000/upload-image"
TOTAL_IMAGES = 2250

print(f"Starting the mosaic stress test with {TOTAL_IMAGES} images...")

for i in range(TOTAL_IMAGES):
    try:
        # 1. Fetch a random 200x200 image
        # Adding a random query param ensures Picsum doesn't give us the same cached image
        img_response = requests.get(f"https://picsum.photos/200?random={i}")

        if img_response.status_code == 200:
            # 2. Package it exactly like an HTML form upload
            files = {
                'file': (f'test_photo_{i}.jpg', io.BytesIO(img_response.content), 'image/jpeg')
            }

            # 3. POST it to your app
            app_response = requests.post(UPLOAD_URL, files=files)

            if app_response.status_code == 200:
                print(f"✅ Uploaded photo {i + 1}/{TOTAL_IMAGES}")
            else:
                print(f"❌ Failed to upload photo {i + 1}")

    except Exception as e:
        print(f"Error on image {i}: {e}")

print("Stress test complete!")