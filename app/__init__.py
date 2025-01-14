# app/__init__.py
from flask import Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

from app.api import routes
