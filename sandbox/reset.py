import argparse
from pathlib import Path
from sandbox.database import DEFAULT_DB
from sandbox.seed import seed_database

def main():
    parser = argparse.ArgumentParser(description='Discard changes and restore the selected local sandbox.')
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    seed_database(args.db, reset=True)
    print(f"Reset sandbox: {args.db.resolve()}")

if __name__ == "__main__":
    main()
