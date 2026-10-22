from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from models import db
from models.user import User

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    """Register a new user"""
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"error": "Email already registered"}), 400

    new_user = User(email=email)
    new_user.set_password(password)

    db.session.add(new_user)
    db.session.commit()

    return jsonify({"message": "User registered successfully"}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate user and return a JWT access token"""
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = User.query.filter_by(email=email).first()

    if user and user.check_password(password):
        # Pass user ID as string in identity
        access_token = create_access_token(identity=str(user.id))
        return jsonify({"access_token": access_token}), 200

    return jsonify({"error": "Invalid email or password"}), 401

@auth_bp.route('/me', methods=['GET', 'PUT'])
@jwt_required()
def user_profile():
    """View or update the authenticated user's profile"""
    current_user_id = int(get_jwt_identity())
    user = User.query.get_or_404(current_user_id)

    if request.method == 'GET':
        return jsonify({
            "id": user.id,
            "email": user.email,
            "is_admin": user.is_admin
        }), 200

    if request.method == 'PUT':
        data = request.get_json()
        new_email = data.get('email')
        new_password = data.get('password')

        if new_email:
            # Check if email is already taken by another user
            existing = User.query.filter_by(email=new_email).first()
            if existing and existing.id != user.id:
                return jsonify({"error": "Email is already in use"}), 400
            user.email = new_email

        if new_password:
            user.set_password(new_password)

        db.session.commit()

        return jsonify({
            "message": "Profile updated successfully",
            "user": {
                "id": user.id,
                "email": user.email,
                "is_admin": user.is_admin
            }
        }), 200
