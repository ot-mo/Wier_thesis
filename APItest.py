from google import genai

client = genai.Client(api_key="AQ.Ab8RN6JcWsxiHj0kqjPGzC6rtAw6okpSc0lExsQ55WxP4235Yg")
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Write a Python PID controller function.",
)
print(response.text)