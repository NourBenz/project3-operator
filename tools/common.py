from pydantic import ValidationError


def failure(code, message, retryable=False):
    return {"ok": False, "data": None,
            "error": {"code": code, "message": message, "retryable": retryable}}


def validation_message(error):
    if not isinstance(error, ValidationError):
        return "Invalid tool arguments"
    entries = error.errors(include_input=False, include_url=False, include_context=False)
    return "; ".join(
        ".".join(str(part) for part in entry["loc"]) + ": " + entry["msg"]
        for entry in entries[:3]
    )
