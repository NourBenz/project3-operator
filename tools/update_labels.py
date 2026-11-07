from sandbox.database import DEFAULT_DB
from sandbox.mock_github import MockGitHub


def update_labels(issue_id=None, labels=None, operation_id=None, expected_version=None,
                  db_path=DEFAULT_DB, *, service=None, task_id="standalone",
                  proposal_id=None, grant=None, context=None, logger=None, **extra):
    backend = service or MockGitHub(db_path, logger=logger)
    return backend.update_labels(issue_id, labels, operation_id, expected_version,
                                 task_id=task_id, proposal_id=proposal_id, grant=grant,
                                 context=context, **extra)
