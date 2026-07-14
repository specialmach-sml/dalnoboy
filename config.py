import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "TEMP_TOKEN")
DB_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/dalnoboy")

INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
