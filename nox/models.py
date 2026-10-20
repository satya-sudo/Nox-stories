from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

PROMPT_VERSION = "phase1.7"
MODEL = "qwen3:4b-instruct-2507-q4_K_M"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brief(StrictModel):
    version: Literal[1] = 1
    genre: Literal["fantasy", "horror", "gentle"]
    premise: str = Field(min_length=20, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    tone: str = Field(default="warm and engaging", max_length=120)
    ending: str = Field(default="complete and satisfying", max_length=160)
    target_minutes: float = Field(default=18, ge=15, le=20)
    scene_count: int = Field(default=7, ge=6, le=8)
    words_per_minute: int = Field(default=155, ge=120, le=180)
    seed: int = Field(default=42, ge=0)


class Voice(StrictModel):
    engine: Literal["kokoro"]
    model: Literal["hexgrad/Kokoro-82M"]
    name: Literal["am_michael"]
    speed: float = Field(ge=0.8, le=1.2)


class Profile(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Literal["nox"]
    version: str
    name: Literal["Nox"]
    language: Literal["en-US"]
    voice: Voice
    identity: str
    system_prompt: str
    genre_rules: dict[str, str]


class Character(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=4, max_length=150)
    facts: list[str] = Field(min_length=1, max_length=3)


class Scene(StrictModel):
    number: int = Field(ge=1, le=8)
    location: str = Field(min_length=2, max_length=100)
    goal: str = Field(min_length=10, max_length=220)
    beats: list[str] = Field(min_length=2, max_length=4)
    start_state: str = Field(min_length=5, max_length=200)
    end_state: str = Field(min_length=5, max_length=200)
    obstacle: str = Field(min_length=5, max_length=180)
    decision: str = Field(min_length=5, max_length=180)
    climax: bool


class Outline(StrictModel):
    title: str = Field(min_length=3, max_length=100)
    premise: str = Field(min_length=10, max_length=400)
    characters: list[Character] = Field(min_length=2, max_length=4)
    world_rules: list[str] = Field(min_length=1, max_length=4)
    resolution: str = Field(min_length=10, max_length=240)
    scenes: list[Scene] = Field(min_length=6, max_length=8)

    @model_validator(mode="after")
    def consistent_identifiers(self):
        names = [c.name.casefold().strip() for c in self.characters]
        if len(set(names)) != len(names) or "nox" in names:
            raise ValueError("Characters must have unique names; Nox cannot be a story character")
        if [s.number for s in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scenes must be in consecutive order")
        if not any(s.climax for s in self.scenes):
            raise ValueError("An outline must contain a climax")
        if re_contains_non_english(self.model_dump_json()):
            raise ValueError("Write all fields in English; do not switch to Chinese or Japanese to fit field lengths")
        return self


def re_contains_non_english(text):
    import re
    return re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text) is not None


class Review(StrictModel):
    approved: bool
    issues: list[str] = Field(max_length=6)
    continuity_summary: str = Field(min_length=5, max_length=600)

    @model_validator(mode="after")
    def decision_matches_issues(self):
        if self.approved == bool(self.issues):
            raise ValueError("Approve only with no issues; a rejection must identify an issue")
        return self


class Framing(StrictModel):
    introduction: str = Field(min_length=20, max_length=600)
    closing: str = Field(min_length=10, max_length=350)
    description: str = Field(min_length=30, max_length=1500)
    tags: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 '&-]{0,59}$")]] = Field(min_length=1, max_length=12)


class Aside(StrictModel):
    text: str = Field(max_length=180)
