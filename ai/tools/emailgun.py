import os
import requests
def send_simple_message():
  	return requests.post(
  		"https://api.mailgun.net/v3/sandboxa3498b09fb294a0aac93dafdb2c0f4a6.mailgun.org/messages",
  		auth=("api", os.getenv('API_KEY', 'API_KEY')),
  		data={"from": "Mailgun Sandbox <postmaster@sandboxa3498b09fb294a0aac93dafdb2c0f4a6.mailgun.org>",
			"to": "Simon R Goldman <input@transparentsf.com>",
  			"subject": "Hello Simon R Goldman",
  			"text": "Congratulations Simon R Goldman, you just sent an email with Mailgun! You are truly awesome!"})