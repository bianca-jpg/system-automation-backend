"""Side-effect-free connection options shared by API, workers and Alembic."""


def alembic_config_url(url: str) -> str:
    """Escapa percentuais para o ConfigParser interno do Alembic.

    Credenciais renderizadas pelo SQLAlchemy usam percent-encoding (por exemplo,
    ``%25``). ``Config.set_main_option`` interpreta ``%`` como interpolacao e
    exige que ele seja duplicado; ao ler a opcao, o Alembic restaura a URL
    original antes de criar a engine.
    """
    return url.replace("%", "%%")


def asyncpg_connect_args(
    *,
    ssl_mode: str,
    connect_timeout_seconds: float,
    command_timeout_seconds: float,
) -> dict[str, object]:
    return {
        "ssl": ssl_mode,
        "timeout": connect_timeout_seconds,
        "command_timeout": command_timeout_seconds,
    }


__all__ = ["alembic_config_url", "asyncpg_connect_args"]
