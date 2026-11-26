from typing import Annotated
from pydantic import IPvAnyAddress, Field, BeforeValidator
from notifig import ConfigSection


IpAddress = Annotated[
    IPvAnyAddress,
    BeforeValidator(
        lambda host: "127.0.0.1" if host == "localhost" else host
    ),
]


class ServerConfig(ConfigSection):
    listen: IpAddress = Field(
        description="The address to bind to.",
        default="localhost",
        # Defaults aren't validated by default
        # This converts the default "localhost" into a typed value
        validate_default=True,
    )

    port: int = Field(
        description="The port to listen on.",
        default=8080,
        # asserts 1 <= port <= 65535
        ge=1,
        le=65535,
    )
