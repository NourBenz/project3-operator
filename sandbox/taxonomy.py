LABEL_DETAILS = {
    "bug": ("type", "Existing behavior fails to meet the documented or demonstrated expectation."),
    "feature": ("type", "A request for new functionality or a deliberate behavior extension."),
    "documentation": ("type", "An issue in guides, examples, wording or reference documentation."),
    "question": ("type", "A request for explanation or usage help rather than a demonstrated defect."),
    "low": ("priority", "Cosmetic or minor impact."),
    "medium": ("priority", "Ordinary work or a defect with a reasonable workaround."),
    "high": ("priority", "Reproducible major functionality failure or significant impact."),
    "critical": ("priority", "Strong evidence of security impact, data loss or widespread outage."),
}
LABELS = tuple(LABEL_DETAILS)
LEGACY_LABELS = {
    "enhancement": "feature", "priority:normal": "medium",
    "priority:low": "low", "priority:medium": "medium",
    "priority:high": "high", "priority:critical": "critical",
}
