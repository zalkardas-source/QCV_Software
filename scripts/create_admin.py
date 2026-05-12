"""
One-time script to create an admin user in the database.
Run from the project root: python scripts/create_admin.py
"""
import sys
import getpass
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.database import engine, Base, SessionLocal
from backend.models import User
from backend.auth import get_password_hash

MIN_PASSWORD_LENGTH = 12


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        email = input("Admin email: ").strip()
        if not email or "@" not in email:
            print("Error: invalid email address.")
            sys.exit(1)

        if db.query(User).filter(User.email == email).first():
            print(f"Error: a user with email '{email}' already exists.")
            sys.exit(1)

        while True:
            password = getpass.getpass("Password (min 12 characters): ")
            if len(password) < MIN_PASSWORD_LENGTH:
                print(f"Password too short — need at least {MIN_PASSWORD_LENGTH} characters. Try again.")
                continue
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Passwords do not match. Try again.")
                continue
            break

        user = User(email=email, hashed_password=get_password_hash(password))
        db.add(user)
        db.commit()
        print(f"Admin user '{email}' created successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
