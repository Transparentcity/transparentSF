import os
import requests
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

# Get the directory of this script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from .env file in the AI directory
dotenv_path = os.path.join(script_dir, '.env')
load_dotenv(dotenv_path)

# Get Perplexity API key from environment variables
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

def test_perplexity_api():
    """Test connection to the Perplexity API"""
    
    print(f"Looking for .env file at: {dotenv_path}")
    
    if not PERPLEXITY_API_KEY:
        print("❌ No Perplexity API key found. Please set PERPLEXITY_API_KEY in your .env file.")
        return False
    else:
        print(f"✅ Found Perplexity API key: {PERPLEXITY_API_KEY[:5]}...")
    
    # Print the curl command to list models 
    print("\n📋 To list available models, run this command:")
    print(f"curl -s https://api.perplexity.ai/chat/models -H \"Authorization: Bearer {PERPLEXITY_API_KEY}\" | python -m json.tool")
    
    # Endpoint URL
    url = "https://api.perplexity.ai/chat/completions"
    
    # Headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}"
    }
    
    # Simple request payload
    payload = {
        "model": "sonar", # Try the simplest model name first
        "messages": [
            {
                "role": "system",
                "content": "Be precise and concise."
            },
            {
                "role": "user",
                "content": "What is the capital of France?"
            }
        ]
    }
    
    print(f"\n🔄 Testing Perplexity API with model: {payload['model']}")
    
    try:
        # Make the API request
        response = requests.post(url, headers=headers, json=payload)
        
        # Check response
        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✅ API call successful!")
            print(f"📝 Response: {content[:100]}...")
            return True
        else:
            print(f"❌ API call failed with status code: {response.status_code}")
            print(f"❌ Error: {response.text}")
            
            # Try with a different model if original fails
            if "model_not_found" in response.text or "invalid_model" in response.text:
                print("\n🔄 Trying with alternative model...")
                
                # Try with different models - keep it simple
                alternative_models = [
                    "mistral-7b-instruct",
                    "llama-3-8b-instruct"
                ]
                
                for model in alternative_models:
                    payload["model"] = model
                    print(f"🔄 Testing with model: {model}")
                    
                    response = requests.post(url, headers=headers, json=payload)
                    if response.status_code == 200:
                        result = response.json()
                        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                        print(f"✅ API call successful with model {model}!")
                        print(f"📝 Response: {content[:100]}...")
                        return True
                    else:
                        print(f"❌ Failed with model {model}: {response.status_code}")
                        print(f"   Error: {response.text[:200]}")
                
                print("❌ All model attempts failed.")
            
            return False
    except Exception as e:
        print(f"❌ Exception occurred: {str(e)}")
        return False

if __name__ == "__main__":
    print(f"🚀 Starting Perplexity API test from {script_dir}...")
    success = test_perplexity_api()
    if success:
        print("✅ Test completed successfully!")
    else:
        print("❌ Test failed!") 