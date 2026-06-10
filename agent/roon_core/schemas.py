from typing import List, Optional

from pydantic import BaseModel, Field


class RoonCoreItemSchema(BaseModel):
    """This schema represents a single search result item from the Roon Core"""

    title: str = Field(..., description="The title of the search result - album, artist, song, action etc.")
    subtitle: Optional[str] = Field("", description="Usually the artist name or additional info")
    image_key: Optional[str] = Field(None, description="The image key for the result's artwork")
    item_key: Optional[str] = Field(None, description="The unique item key in Roon for this result, used for further drilling-down searches or actions")
    hint: Optional[str] = Field(None, description="A hint about the type of result - Album, Artist, Track, Playlist, Compilation, Action List, Action etc.")
    item_key_path: List[str] = Field(default_factory=list, description="Full item_key chain from search root to this item")
    source_group: Optional[str] = Field(None, description="Group label set during collation (e.g. parent album/playlist title)")

class RoonCoreListSchema(BaseModel):
    """This schema represents additional info about the search result list from the Roon Core"""

    count: int = Field(..., description="Total number of items in the search result")
    display_offset: Optional[int] = Field(None, description="Offset from the beginning of the total result set")
    hint: Optional[str] = Field(None, description="A hint about what the list contains, e.g. 'action_list' denotes a list of actions whose item_key will activate that action.")
    image_key: Optional[str] = Field(None, description="The image key for the result's artwork")
    level: Optional[int] = Field(None, description="The drill-down level of the search results")
    subtitle: Optional[str] = Field("", description="Subtitle for the result list, e.g. media code, list of artists, etc.")
    title: Optional[str] = Field(None, description="Title for the result list, e.g. what the results pertain to such as list of albums, the album the list of tracks belongs to, the song the actions are for, etc.")

class RoonCoreResultsSchema(BaseModel):
    """This schema represents what is returned from the Roon Core after a search or drill-down operation"""

    items: List[RoonCoreItemSchema] = Field(..., description="List of search result items")
    list: Optional[RoonCoreListSchema] = Field(None, description="More info about the items")
    search_attempts: int = Field(1, exclude=True)
    search_retry_notes: Optional[List[str]] = Field(None, exclude=True)

class RoonCoreItemSummarySchema(BaseModel):
    """ Schema defining the summary of a Roon Core result item """

    title: str = Field(..., description="Artist, album, track or action name")
    group: Optional[str] = Field(None, description="Title of album or playlist for this group")
    extra_info: Optional[str] = Field(None, description="Additional info such as artist name(s)")
    reference: str = Field(..., description="A unique identifier for this result, used for further drilling-down searches or actions")
    intended_category: Optional[str] = Field(None, description="Per-item category hint (e.g. 'track', 'album', 'playlist'). Overrides the request-level intended_item_category.")

class RoonCoreResultsGroupSchema(BaseModel):
    """ Group of result summaries from the Roon Core """

    group: Optional[str] = Field(None, description="Title of album or playlist for this group")
    items: List[RoonCoreItemSummarySchema] = Field(..., description="Items in this group")
