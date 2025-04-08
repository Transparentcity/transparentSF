import os
from openai import OpenAI

def test_openai_api_key():
    """
    Quick test to verify that the environment variables and API key are set up correctly
    and that a call to OpenAI can be made successfully.
    """
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise ValueError("OpenAI API key not found in environment variables.")
    
    client = OpenAI(api_key=openai_api_key)

    try:
        response = client.completions.create(
            model="gpt-3.5-turbo-instruct",
            prompt="Say hello, OpenAI!",
            max_tokens=5
        )
        print("API call successful. Response:", response.choices[0].text.strip())
    except Exception as e:
        print("API call failed. Error:", str(e))

# Run the test function
test_openai_api_key()
