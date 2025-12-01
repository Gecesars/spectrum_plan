import sys
import os
import traceback
from unittest.mock import MagicMock, patch
from flask import Flask

# Add the project root to the python path
sys.path.append(os.getcwd())

from app import create_app
from app.config import Config
from app.models import User

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False

def test_auth_flow_mocked():
    print("Testing Auth Flow (Mocked)...")
    
    app = create_app(TestConfig)
    
    with app.test_client() as client:
        # Mock get_session to return a mock session
        with patch("app.api.routes_auth.get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__.return_value = mock_session
            
            # 1. Test Registration
            print("1. Testing Registration...")
            # Mock query to return None (no existing user)
            mock_session.query.return_value.filter_by.return_value.first.return_value = None
            
            with patch("app.api.routes_auth.send_verification_email") as mock_send_email:
                try:
                    response = client.post("/auth/register", json={
                        "email": "test@example.com",
                        "password": "password123",
                        "full_name": "Test User"
                    })
                    print(f"   Response status: {response.status_code}")
                    print(f"   Response data: {response.data}")
                    
                    assert response.status_code == 201
                    assert b"Registration successful" in response.data
                    mock_send_email.assert_called_once()
                    mock_session.add.assert_called_once()
                    print("   Registration PASSED")
                except Exception:
                    traceback.print_exc()
                    raise

            # 2. Test Login
            print("2. Testing Login...")
            # Mock query to return a user with correct password hash
            from werkzeug.security import generate_password_hash
            mock_user = MagicMock(spec=User)
            mock_user.password_hash = generate_password_hash("password123")
            mock_user.email = "test@example.com"
            mock_user.is_active = True
            mock_user.is_authenticated = True
            mock_user.get_id.return_value = "1"
            
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_user
            
            # We also need to patch login_user because it relies on user_loader which uses DB
            with patch("app.api.routes_auth.login_user") as mock_login_user:
                try:
                    response = client.post("/auth/login", json={
                        "email": "test@example.com",
                        "password": "password123"
                    })
                    print(f"   Response status: {response.status_code}")
                    
                    assert response.status_code == 200
                    mock_login_user.assert_called_once_with(mock_user)
                    print("   Login PASSED")
                except Exception:
                    traceback.print_exc()
                    raise

            # 3. Test Verification
            print("3. Testing Verification...")
            # Mock query to return user by token
            mock_user_verify = MagicMock(spec=User)
            mock_user_verify.is_verified = False
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_user_verify
            
            try:
                response = client.get("/auth/verify/some-token", follow_redirects=True)
                print(f"   Response status: {response.status_code}")
                
                assert response.status_code == 200
                assert mock_user_verify.is_verified is True
                print("   Verification PASSED")
            except Exception:
                traceback.print_exc()
                raise

if __name__ == "__main__":
    try:
        test_auth_flow_mocked()
        print("\nAll auth tests passed successfully!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        exit(1)
