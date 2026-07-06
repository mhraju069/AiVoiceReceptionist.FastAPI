from database import engine
from sqlalchemy import text, inspect

def migrate():
    with engine.connect() as conn:
        print("🔍 Checking columns in call_logs...")
        try:
            inspector = inspect(engine)
            if not inspector.has_table("call_logs"):
                print("ℹ️ Table 'call_logs' does not exist yet. It will be created automatically on application startup.")
                return

            columns = [col['name'] for col in inspector.get_columns('call_logs')]
            
            # Check and add 'reason' column
            if 'reason' not in columns:
                conn.execute(text("ALTER TABLE call_logs ADD COLUMN reason VARCHAR;"))
                print("✅ Added 'reason' column.")
            else:
                print("ℹ️ Column 'reason' already exists.")
            
            # Check and add 'duration' column
            if 'duration' not in columns:
                conn.execute(text("ALTER TABLE call_logs ADD COLUMN duration INTEGER;"))
                print("✅ Added 'duration' column.")
            else:
                print("ℹ️ Column 'duration' already exists.")
            
            conn.commit()
            print("🚀 Migration completed successfully!")
        except Exception as e:
            print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    migrate()
