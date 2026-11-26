from pydantic import Field, SecretStr

from notifig import ConfigSection, DynamicSection


class UserAuth(ConfigSection):
    """User credentials for a single user."""

    username: str = Field(description="Login username")
    password: SecretStr = Field(description="Login password")


class UsersConfig(DynamicSection[UserAuth]):
    """Known users identities."""
