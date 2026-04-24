import requests

# 🔑 Replace these
ACCESS_TOKEN = "EAAMb3f3lXIwBQd4ymHZCZAN4Y0OmSZCGQF6ZCZBfBhTvsnh4QmAEwy7vgVr9zQebVRW3fPhmxYVxqUasYbfpKU2IeFDh8haZBlZAXQmzI1XNprIXx40g2jTg3GJihNvHJb2lJPPL4buAY1VpyUjJEnQ68vPANsW1ZAsOAZBfLMxOfuGdceg5D3XOisnpzDndXCAZDZD"
PHONE_NUMBER_ID = "899078993293705"
TO_PHONE_NUMBER = "919061293580"  # Basil's number

url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

data = {
    "messaging_product": "whatsapp",
    "to": TO_PHONE_NUMBER,
    "type": "interactive",
    "interactive": {
        "type": "cta_url",
        "body": {
            "text": """Hi Basil 😊

We hope you had a wonderful stay at Paradise Beach Resort Cherai!

Your review means the world to us and helps other travellers find us.
Tap below to leave a quick review — it only takes a minute! 🙏

Have any feedback? Just reply here and we'll make it right."""
        },
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": "Leave a Review ⭐",
                "url": "https://www.google.com/maps/place/Pete's+Inn+Homestay/@10.1674977,76.3778973,17z/data=!4m11!3m10!1s0x3b08079f023f1189:0x5db042313f5a17b!5m2!4m1!1i2!8m2!3d10.1674924!4d76.3804722!9m1!1b1!16s%2Fg%2F11h0mwylr1?entry=ttu"
            }
        }
    }
}
import time

time.sleep(15)  # Wait for 5 seconds before sending the message
response = requests.post(url, headers=headers, json=data)

print(response.status_code)
print(response.json())