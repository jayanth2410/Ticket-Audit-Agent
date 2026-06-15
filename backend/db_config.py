#import dotenv to load environment variables from .env file
import os
from dotenv import load_dotenv
from db_modal import DatabaseSession

load_dotenv()

class DBConfig:

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

        if not self.database_url:
            raise ValueError("DATABASE_URL not found in environment variables")
        
        # Initialize database session manager
        self.db_session = DatabaseSession(self.database_url)
        self.db_session.initialize()
    
    def get_session(self):
        """Get a database session"""
        return self.db_session.get_session()
    
    def close(self):
        """Close database connection"""
        self.db_session.close()
