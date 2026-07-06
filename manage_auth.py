import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from database import Base
from models.auth_models import User
from utils.auth_utils import get_password_hash

# Load environment variables
load_dotenv()

def main():
    print("==========================================")
    print("   Pay Minimum Tax CLI Authentication Manager")
    print("==========================================")

    env_db_url = os.getenv("DATABASE_URL")
    db_urls = []
    if env_db_url:
        db_urls.append(env_db_url)
    else:
        db_urls.extend([
            "postgresql://postgres:postgres@localhost:5432/vocaai",
            "postgresql://postgres:postgres@db:5432/vocaai"
        ])

    engine = None
    last_error = None
    
    for url in db_urls:
        try:
            connect_args = {}
            if url.startswith("sqlite"):
                connect_args["check_same_thread"] = False
            test_engine = create_engine(url, connect_args=connect_args)
            with test_engine.connect() as conn:
                pass
            engine = test_engine
            break
        except Exception as e:
            last_error = e
            continue

    if not engine:
        print("\nError: Could not connect to the database.")
        print(f"Details of the error: {last_error}")
        print("\nPlease ensure that:")
        print("1. Your database (PostgreSQL/SQLite) is properly configured.")
        print("2. If using PostgreSQL, verify your Docker container or server is running.")
        print("3. Check the DATABASE_URL environment variable in your .env file.\n")
        return

    # Automatically add missing columns dynamically
    with engine.connect() as conn:
        try:
            inspector = inspect(engine)
            if inspector.has_table("users"):
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'otp' not in columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN otp VARCHAR;"))
                    print("✅ Added 'otp' column to users table.")
                if 'otp_expires_at' not in columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN otp_expires_at TIMESTAMP;"))
                    print("✅ Added 'otp_expires_at' column to users table.")
                conn.commit()
        except Exception as e:
            print(f"⚠️ Warning updating users schema: {e}")

    # Create all tables if they do not exist
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        print("\nWhat would you like to do?")
        print("1. Register a new user")
        print("2. Change password of an existing user")
        print("3. Delete an existing user")
        
        choice = input("\nEnter your choice (1, 2, or 3): ").strip()
        
        if choice == '1':
            print("\n--- Register a New User ---")
            email = input("Enter email address: ").strip()
            name = input("Enter name: ").strip()
            password = input("Enter password: ").strip()
            confirm_password = input("Confirm password: ").strip()
            
            if not email or not password or not confirm_password:
                print("Error: Email, password, and confirm password are required.")
                return

            if password != confirm_password:
                print("Error: Passwords do not match.")
                return

            existing_user = db.query(User).filter(User.email == email).first()
            if existing_user:
                print("Error: A user with this email address already exists.")
                return

            hashed_password = get_password_hash(password)
            new_user = User(
                email=email,
                name=name,
                hashed_password=hashed_password
            )
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            print(f"\nSuccess: User '{email}' created successfully in the database!")

        elif choice == '2':
            print("\n--- Change Password ---")
            email = input("Enter email address: ").strip()
            if not email:
                print("Error: Email is required.")
                return

            user = db.query(User).filter(User.email == email).first()
            if not user:
                print("Error: User with this email address does not exist.")
                return

            new_password = input("Enter your new password: ").strip()
            confirm_password = input("Confirm your new password: ").strip()
            
            if not new_password or not confirm_password:
                print("Error: Password and confirm password cannot be empty.")
                return

            if new_password != confirm_password:
                print("Error: Passwords do not match.")
                return

            user.hashed_password = get_password_hash(new_password)
            db.commit()
            print(f"\nSuccess: Password for '{email}' has been successfully changed!")

        elif choice == '3':
            print("\n--- Delete User ---")
            email = input("Enter the email address of the user to delete: ").strip()
            if not email:
                print("Error: Email is required.")
                return

            user = db.query(User).filter(User.email == email).first()
            if not user:
                print("Error: User with this email address does not exist.")
                return

            confirm = input(f"Are you sure you want to delete the user '{email}'? (yes/no): ").strip().lower()
            if confirm == 'yes' or confirm == 'y':
                db.delete(user)
                db.commit()
                print(f"\nSuccess: User '{email}' has been deleted from the database!")
            else:
                print("Deletion cancelled.")

        else:
            print("Invalid choice. Exiting.")

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
