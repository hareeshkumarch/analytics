import hashlib


def table_key(table_name: str) -> str:
    return "t_" + hashlib.sha256(table_name.encode("utf-8")).hexdigest()[:16]


def table_artifact_id(artifact_id: str, table_name: str) -> str:
    return f"{artifact_id}__{table_key(table_name)}"
