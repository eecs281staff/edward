from dotenv import dotenv_values

ENV_FILE = ".env"

def get_env_variable(key: str) -> str:
    val =  dotenv_values(ENV_FILE)[key]
    if not val:
        raise ValueError(f"key {key} not found in environment")

    return val


def get_optional_env_variable(key: str) -> str | None:
    return dotenv_values(ENV_FILE).get(key)


def get_float_env_variable(key: str, default: float) -> float:
    val = get_optional_env_variable(key)
    if not val:
        return default

    return float(val)


def get_bool_env_variable(key: str, default: bool) -> bool:
    val = get_optional_env_variable(key)
    if not val:
        return default

    if val.lower() == "true":
        return True

    if val.lower() == "false":
        return False

    raise ValueError(f"key {key} must be true or false, got {val}")
