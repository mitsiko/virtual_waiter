import requests, json

url = "http://localhost:11434/api/chat"
payload = {
    "model": "qwen2.5:3b",
    "messages": [
        {"role": "user", "content": "What's the weather in Manila?"}
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"}
                    },
                    "required": ["city"]
                }
            }
        }
    ],
    "stream": False
}

r = requests.post(url, json=payload)
print(json.dumps(r.json(), indent=2))