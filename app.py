import os
from flask import Flask
from flask_jwt_extended import JWTManager
from models import db
from blueprints.auth import auth_bp
from blueprints.products import products_bp

def create_app():
    app = Flask(__name__)
    
    # Database configuration (PostgreSQL on Render, fallback to SQLite locally)
    database_url = os.environ.get("DATABASE_URL", "sqlite:///ecommerce.db")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
        
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # JWT Configuration
    app.config['JWT_SECRET_KEY'] = os.environ.get("JWT_SECRET_KEY", "dev-secret-key-change-this")
    
    # Initialize extensions
    db.init_app(app)
    JWTManager(app)
    
    # Register Blueprints
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(products_bp, url_prefix='/api/v1/products')
    
    with app.app_context():
        db.create_all()
        
    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
