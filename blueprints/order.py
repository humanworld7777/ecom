from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db
from models.order import Order, OrderItem
from models.product import Product
from models.user import User

orders_bp = Blueprint('orders', __name__)

def is_admin():
    """Helper function to check if the current JWT holder is an admin"""
    current_user_id = get_jwt_identity()
    user = User.query.get(current_user_id)
    return user and user.is_admin

@orders_bp.route('/', methods=['POST'])
@jwt_required()
def create_order():
    """Place a new order for the authenticated user"""
    current_user_id = int(get_jwt_identity())
    data = request.get_json()
    items_data = data.get('items', []) # Expects a list of {"product_id": 1, "quantity": 2}

    if not items_data:
        return jsonify({"error": "No items provided in the order"}), 400

    calculated_total = 0
    order_items_to_create = []

    # Verify products, stock, and compute totals server-side
    for item in items_data:
        product_id = item.get('product_id')
        quantity = item.get('quantity', 1)

        if quantity <= 0:
            return jsonify({"error": "Quantity must be greater than zero"}), 400

        product = Product.query.get(product_id)
        if not product:
            return jsonify({"error": f"Product with ID {product_id} not found"}), 404

        if product.stock < quantity:
            return jsonify({"error": f"Insufficient stock for product: {product.name}"}), 400

        # Deduct stock
        product.stock -= quantity
        
        item_total = product.price * quantity
        calculated_total += item_total

        order_items_to_create.append(
            OrderItem(
                product_id=product.id,
                quantity=quantity,
                price=product.price
            )
        )

    # Create the main order record
    new_order = Order(
        user_id=current_user_id,
        total_amount=calculated_total,
        status='pending'
    )
    
    db.session.add(new_order)
    db.session.flush() # Flushes to generate new_order.id before committing items

    for order_item in order_items_to_create:
        order_item.order_id = new_order.id
        db.session.add(order_item)

    db.session.commit()

    return jsonify({
        "message": "Order placed successfully",
        "order": new_order.to_dict()
    }), 201


@orders_bp.route('/', methods=['GET'])
@jwt_required()
def get_user_orders():
    """Get all orders belonging to the authenticated user (or all if admin)"""
    current_user_id = int(get_jwt_identity())
    user = User.query.get(current_user_id)

    if user.is_admin:
        orders = Order.query.all()
    else:
        orders = Order.query.filter_by(user_id=current_user_id).all()

    return jsonify([order.to_dict() for order in orders]), 200


@orders_bp.route('/<int:order_id>/status', methods=['PUT'])
@jwt_required()
def update_order_status(order_id):
    """Admin-only endpoint to update an order's fulfillment status"""
    if not is_admin():
        return jsonify({"error": "Admin privileges required"}), 403

    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    new_status = data.get('status')

    allowed_statuses = ['pending', 'shipped', 'completed', 'cancelled']
    if new_status not in allowed_statuses:
        return jsonify({"error": f"Invalid status. Choose from {allowed_statuses}"}), 400

    order.status = new_status
    db.session.commit()

    return jsonify({
        "message": "Order status updated successfully",
        "order": order.to_dict()
    }), 200
