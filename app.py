import os
from flask import Flask
from waitress import serve

app = Flask(__name__)

@app.route('/')
def hello():
    return "App is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    serve(app, host="0.0.0.0", port=port)