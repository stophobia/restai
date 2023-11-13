from pydantic import BaseModel
from typing import Union


class IngestModel(BaseModel):
    url: str
    recursive: bool = False
    depth: int = 2


class EmbeddingModel(BaseModel):
    source: Union[str, None] = None


class QuestionModel(BaseModel):
    question: str
    llm: Union[str, None] = None
    system: Union[str, None] = None


class ChatModel(BaseModel):
    message: str
    id: Union[str, None] = None


class ProjectModel(BaseModel):
    name: str
    embeddings: Union[str, None] = None
    llm: Union[str, None] = None
    system: Union[str, None] = None


class UserProject(BaseModel):
    id: int
    owner_id: int

    class Config:
        from_attributes = True


class User(BaseModel):
    id: int
    username: str | None = None
    is_admin: bool = False
    projects: list[UserProject] = []

    class Config:
        from_attributes = True
