from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class ProjectRead(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    document_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ParseRequest(BaseModel):
    force: bool = False


class ModelEdit(BaseModel):
    expected_version: int = Field(ge=1)
    model: dict


class ConfirmRequest(BaseModel):
    acknowledge_issues: bool = False


class Module(BaseModel):
    id: str
    name: str
    simulink_type: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    description: str = ""
    origin: Literal["extracted", "inferred", "user_input", "default_assumption"] = "inferred"
    evidence: list[str] = Field(default_factory=list)


class Parameter(BaseModel):
    id: str
    owner_id: str | None = None
    name: str
    value: str | float | int | None = None
    unit: str = ""
    status: Literal[
        "confirmed_from_document",
        "inferred",
        "needs_confirmation",
        "missing",
        "unit_unknown",
        "multiple_candidates",
    ] = "needs_confirmation"
    origin: Literal["extracted", "inferred", "user_input", "default_assumption"] = "inferred"
    evidence: list[str] = Field(default_factory=list)


class Connection(BaseModel):
    source_module: str
    source_port: str
    target_module: str
    target_port: str
    signal_name: str = ""
    meaning: str = ""
    evidence: list[str] = Field(default_factory=list)


class ModelSnapshot(BaseModel):
    schema_version: str = "1.0"
    system_name: str
    domain: str = "unknown"
    control_objective: str = ""
    input_signals: list[dict] = Field(default_factory=list)
    output_signals: list[dict] = Field(default_factory=list)
    modules: list[Module] = Field(default_factory=list)
    parameters: list[Parameter] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    equations: list[dict] = Field(default_factory=list)
    simulation_config: dict = Field(default_factory=dict)
    assumptions: list[dict] = Field(default_factory=list)
    open_questions: list[dict] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    buildability_status: Literal[
        "可直接搭建", "补充参数后可搭建", "结构存在歧义", "仅供参考"
    ] = "仅供参考"


class ExportRequest(BaseModel):
    model_version_id: str
    formats: list[Literal["svg", "csv", "md", "json"]] = Field(
        default_factory=lambda: ["svg", "csv", "md", "json"]
    )
