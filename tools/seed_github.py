import argparse
import json
import os
from pathlib import Path

from sandbox.taxonomy import LABEL_DETAILS
from tools.github_service import GitHubAPIError, GitHubClient


FIXTURES = Path(__file__).resolve().parents[1] / "sandbox" / "seed_issues.json"
LABEL_COLORS = {
    "bug": "d73a4a", "feature": "a2eeef", "documentation": "0075ca", "question": "d876e3",
    "low": "cfd3d7", "medium": "fbca04", "high": "e4e669", "critical": "b60205",
}


def marker(fixture_id):
    return f"<!-- operator-fixture:{fixture_id} -->"


def load_fixtures(path=FIXTURES):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def existing_issues(client):
    found = {}
    for page in range(1, 21):
        items = client.request("GET", f"/issues?state=all&per_page=100&page={page}")
        if not items:
            break
        for item in items:
            if "pull_request" in item:
                continue
            body = item.get("body") or ""
            for fixture in load_fixtures():
                tag = marker(fixture["id"])
                if tag in body:
                    found[fixture["id"]] = item
        if len(items) < 100:
            break
    return found


def ensure_labels(client, apply):
    current = {item["name"] for item in client.request("GET", "/labels?per_page=100")}
    missing = [name for name in LABEL_DETAILS if name not in current]
    if apply:
        for name in missing:
            client.request("POST", "/labels", {"name": name, "color": LABEL_COLORS[name], "description": LABEL_DETAILS[name][1]})
    return missing


def seed(repository, *, token=None, apply=False, fixture_path=FIXTURES, limit=None):
    client = GitHubClient(repository, token)
    fixtures = load_fixtures(fixture_path)
    if limit is not None:
        fixtures = fixtures[:limit]
    existing = existing_issues(client)
    missing_labels = ensure_labels(client, apply)
    actions = []
    for fixture in fixtures:
        if fixture["id"] in existing:
            actions.append({"fixture_id": fixture["id"], "action": "skip", "issue_number": existing[fixture["id"]]["number"]})
            continue
        body = f"{fixture['body'].rstrip()}\n\n{marker(fixture['id'])}"
        action = {"fixture_id": fixture["id"], "action": "create", "title": fixture["title"], "labels": fixture["labels"]}
        if apply:
            issue = client.request("POST", "/issues", {"title": fixture["title"], "body": body, "labels": fixture["labels"]})
            number = issue["number"]
            for comment in fixture.get("comments", []):
                client.request("POST", f"/issues/{number}/comments", {"body": comment["body"]})
            if fixture.get("state") == "closed":
                client.request("PATCH", f"/issues/{number}", {"state": "closed"})
            action["issue_number"] = number
        actions.append(action)
    return {"repository": repository, "apply": apply, "missing_labels": missing_labels, "actions": actions}


def main():
    parser = argparse.ArgumentParser(description="Plan or create deduplicated GitHub issue fixtures.")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--fixture-file", type=Path, default=FIXTURES)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true", help="Actually create missing labels and issues")
    args = parser.parse_args()
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    try:
        print(json.dumps(seed(args.repository, apply=args.apply, fixture_path=args.fixture_file, limit=args.limit), indent=2))
    except (GitHubAPIError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
