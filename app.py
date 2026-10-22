import os
from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from models import db
from blueprints.auth import auth_bp
from blueprints.products import products_bp
from blueprints.orders import orders_bp

def create_app():
    """Application factory pattern for creating the Flask app"""
    app = Flask(__name__)

    # Database and Security Configuration using Environment Variables
    database_uri = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce.db')
    
    # Render provides postgres:// which SQLAlchemy 2.0+ needs as postgresql://
    if database_uri and database_uri.startswith("postgres://"):
        database_uri = database_uri.replace("postgres://", "postgresql://", 1)

    app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'fallback-secret-key')

    # Initialize Extensions
    db.init_app(app)
    migrate = Migrate(app, db)
    jwt = JWTManager(app)

    # Register Blueprints with URL prefixes
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(products_bp, url_prefix='/api/v1/products')
    app.register_blueprint(orders_bp, url_prefix='/api/v1/orders')

    # --- Global Error Handlers ---
    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({"error": "Bad Request", "message": str(error.description)}), 400

    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({"error": "Unauthorized", "message": "Missing or invalid token"}), 401

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({"error": "Forbidden", "message": "You do not have permission to access this resource"}), 403

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not Found", "message": "The requested resource or endpoint does not exist"}), 404

    @app.errorhandler(500)
    def internal_server_error(error):
        db.session.rollback()
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred on the server"}), 500

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
