import argparse
import json
from pathlib import Path

from agent.logging_utils import logged_call
from agent.schemas import GetLabelsArgs
from sandbox.database import DEFAULT_DB, connect


def get_labels(db_path=DEFAULT_DB, *, context=None, logger=None, **extra):
    def execute():
        GetLabelsArgs.model_validate(extra)
        with connect(db_path, readonly=True) as db:
            taxonomy = [dict(row) for row in db.execute(
                "SELECT name,category,description FROM labels WHERE category IN ('type','priority') ORDER BY name",
            )]
            return {"ok": True, "data": {"labels": [row["name"] for row in taxonomy],
                                        "taxonomy": taxonomy}, "error": None}
    return logged_call("get_labels", extra, execute, context=context, logger=logger)


def main():
    parser = argparse.ArgumentParser(description="Read allowed labels from the local sandbox.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    result = get_labels(args.db)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
