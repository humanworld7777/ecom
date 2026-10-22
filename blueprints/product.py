from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db
from models.product import Product
from models.user import User

products_bp = Blueprint('products', __name__)

def is_admin():
    """Helper function to check if the current JWT holder is an admin"""
    current_user_id = get_jwt_identity()
    user = User.query.get(current_user_id)
    return user and user.is_admin

@products_bp.route('/', methods=['GET'])
def get_products():
    """Public endpoint to list all products"""
    products = Product.query.all()
    return jsonify([p.to_dict() for p in products]), 200

@products_bp.route('/<int:product_id>', methods=['GET'])
def get_product(product_id):
    """Public endpoint to view a single product by ID"""
    product = Product.query.get_or_404(product_id)
    return jsonify(product.to_dict()), 200

@products_bp.route('/', methods=['POST'])
@jwt_required()
def create_product():
    """Admin-only endpoint to create a new product"""
    if not is_admin():
        return jsonify({"error": "Admin privileges required"}), 403

    data = request.get_json()
    name = data.get('name')
    price = data.get('price')
    stock = data.get('stock', 0)
    description = data.get('description', '')

    if not name or price is None:
        return jsonify({"error": "Product name and price are required"}), 400

    new_product = Product(
        name=name,
        price=price,
        stock=stock,
        description=description
    )
    
    db.session.add(new_product)
    db.session.commit()

    return jsonify({
        "message": "Product created successfully",
        "product": new_product.to_dict()
    }), 201
