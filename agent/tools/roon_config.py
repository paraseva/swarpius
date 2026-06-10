from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exceptions import ToolConfigurationError

RoonConfigActions = Literal[
    "Set Default Zone",
    "Set Zone Alias",
    "Remove Zone Alias",
    "Clear All Zone Aliases",
    "Get Default Zone",
    "Get Zone Aliases",
    "Transfer Zone",
    "Rename Zone Alias",
    "Group Zones",
    "Ungroup Zones",
    "Get Groups",
]

class RoonConfigToolInputSchema(BaseModel):
    """
    Schema for configuring Roon settings.
    """

    action: RoonConfigActions = Field(
        ...,
        description=(
            "The config action to perform in Roon"
        ),
    )
    zone: Optional[str] = Field(
        None,
        description="The zone in Roon to target the action at",
    )
    zone_to_transfer_to: Optional[str] = Field(
        None,
        description="The zone to transfer to (only required for Transfer Zone action)",
    )
    alias: Optional[str] = Field(
        None,
        description="The zone alias name (for Set/Remove/Rename Zone Alias). Also accepted by Ungroup Zones to address a group by an alias of one of its members.",
    )
    new_name: Optional[str] = Field(
        None,
        description="New name for Rename Zone Alias",
    )
    group_zones: Optional[list[str]] = Field(
        None,
        description="List of zone names to group together (required for Group Zones, at least 2)",
    )

    @model_validator(mode="after")
    def check_correct_fields_provided(self) -> "RoonConfigToolInputSchema":
        if self.action == "Set Default Zone" and not self.zone:
            raise ValueError("Zone must be provided for Set Default Zone action")
        if self.action == "Set Zone Alias" and (not self.alias or not self.zone):
            raise ValueError("Alias and zone must be provided for Set Zone Alias action")
        if self.action == "Transfer Zone" and not self.zone_to_transfer_to:
            raise ValueError("The zone_to_transfer_to must be provided for Transfer Zone action")
        if self.action == "Remove Zone Alias" and not self.alias:
            raise ValueError("Alias must be provided for Remove Zone Alias action")
        if self.action == "Rename Zone Alias" and (not self.alias or not self.new_name):
            raise ValueError("Current alias and new_name required for Rename Zone Alias")
        if self.action == "Group Zones" and (not self.group_zones or len(self.group_zones) < 2):
            raise ValueError("At least two zone names (group_zones) required for Group Zones")
        if self.action == "Ungroup Zones" and not self.alias and not self.zone:
            raise ValueError("Provide a zone or alias to ungroup")
        return self

class RoonConfigToolOutputSchema(BaseModel):
    """This schema details the requested information, or result of the configuration performed."""

    result: str = Field(..., description="Information requested or description of result of action")
    error: Optional[str] = Field(None, description="Error message if the action was not successful")

class RoonConfigToolConfig(BaseModel):

    model_config = ConfigDict(arbitrary_types_allowed=True)
    roon_connection: Optional[Any] = None
    perform_config_action: Optional[Callable[..., str]] = None


class RoonConfigTool:
    """
    Tool for performing configuration actions on Roon such as setting default zone, setting zone alias, etc.
    """

    input_schema = RoonConfigToolInputSchema
    output_schema = RoonConfigToolOutputSchema
    parallel_safe = False

    def __init__(self, config: RoonConfigToolConfig = RoonConfigToolConfig()) -> None:
        self.config = config
        self.roon_connection = config.roon_connection
        self.perform_config_action = config.perform_config_action

    async def run_async(
        self, params: RoonConfigToolInputSchema
    ) -> RoonConfigToolOutputSchema:
        zone = params.zone
        zone_to_transfer_to = params.zone_to_transfer_to
        action = params.action
        alias = params.alias
        new_name = params.new_name
        group_zones_param = params.group_zones

        try:
            if not self.perform_config_action:
                raise ToolConfigurationError("Configuration action handler is not available")
            result: str = self.perform_config_action(
                action,
                zone,
                zone_to_transfer_to,
                alias,
                group_zones=group_zones_param,
                new_name=new_name,
            )
            action_outcome = RoonConfigToolOutputSchema(result=result)
        except Exception as exc:
            action_outcome = RoonConfigToolOutputSchema(
                result=str(exc),
                error=str(exc),
            )

        return action_outcome
