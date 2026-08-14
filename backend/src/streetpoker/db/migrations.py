def escape_alembic_config_value(value: str) -> str:
    """Escape percent signs consumed by Alembic's ConfigParser interpolation."""
    return value.replace("%", "%%")
