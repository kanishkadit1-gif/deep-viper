import os
from pathlib import Path
from dotenv import load_dotenv

# Load this folder's .env, then fall back to the project-root .env (where the
# shared OPENAI_API_KEY already lives) so this system runs on the project key.
load_dotenv()
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# LLM backend: "openai" (default, uses the project's OPENAI_API_KEY) | "gemini".
LLM_BACKEND = os.getenv("CHESS_LLM_BACKEND", "openai")

MODEL = "gemini-3.1-flash-lite"          # used only when LLM_BACKEND == "gemini"
OPENAI_MODEL = os.getenv("CHESS_OPENAI_MODEL", "gpt-5.4")

MAX_MOVES = 5
MAX_RETRIES = 3
PLAYER_B_MODE = "llm"
LOG_FILE = "log.txt"
LOG_LEVEL = "INFO"
ARM_DIR = "arm_moves"
ARM_ACK = False

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
