from dotenv import dotenv_values

ENV_FILE = ".env"

def get_env_variable(key: str) -> str:
    val =  dotenv_values(ENV_FILE)[key]
    if not val:
        raise ValueError(f"key {key} not found in environment")

    return val


def get_optional_env_variable(key: str) -> str | None:
    return dotenv_values(ENV_FILE).get(key)
