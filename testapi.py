import unittest
import json
from app import create_app
from models import db
from models.user import User
from models.product import Product

class EcommerceAPITestCase(unittest.TestCase):
    
    def setUp(self):
        """Set up a fresh test client and in-memory database before each test"""
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()
        
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        """Clean up the database after each test"""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_user_registration_and_login(self):
        """Test user signup and token generation"""
        # 1. Register a new user
        reg_response = self.client.post('/api/v1/auth/register', json={
            "email": "user@test.com",
            "password": "password123"
        })
        self.assertEqual(reg_response.status_code, 201)
        
        # 2. Login with the user
        login_response = self.client.post('/api/v1/auth/login', json={
            "email": "user@test.com",
            "password": "password123"
        })
        self.assertEqual(login_response.status_code, 200)
        data = json.loads(login_response.data)
        self.assertIn("access_token", data)

    def test_product_creation_and_listing(self):
        """Test creating a product and fetching the catalog"""
        with self.app.app_context():
            # Create an admin user directly in the test database
            admin = User(email="admin@test.com", is_admin=True)
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()

        # Login as admin to get token
        login_response = self.client.post('/api/v1/auth/login', json={
            "email": "admin@test.com",
            "password": "admin123"
        })
        token = json.loads(login_response.data)["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create a product
        prod_response = self.client.post('/api/v1/products/', json={
            "name": "Test Laptop",
            "description": "High performance",
            "price": 999.99,
            "stock": 5
        }, headers=headers)
        self.assertEqual(prod_response.status_code, 201)

        # Fetch product list publicly
        get_response = self.client.get('/api/v1/products/')
        self.assertEqual(get_response.status_code, 200)
        products = json.loads(get_response.data)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "Test Laptop")

if __name__ == '__main__':
    unittest.main()
