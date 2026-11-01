import os
import sys
from pathlib import Path


def run():
    """Read generated credentials and exec a service without shell interpolation."""
    password = Path(os.getenv("DATABASE_PASSWORD_FILE", "/runtime/postgres-password")).read_text().strip()
    os.environ["DATABASE_URL"] = f"postgresql://oracle:{password}@postgres:5432/oracle"
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    run()
