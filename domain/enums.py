"""Canonical domain enumerations for transmission tower detailing."""
from __future__ import annotations
from enum import Enum


class SectionType(str, Enum):
    ANGLE = "ANGLE"
    FLAT = "FLAT"
    PLATE = "PLATE"
    HT_ANGLE = "HT_ANGLE"
    HT_FLAT = "HT_FLAT"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, value: str | None) -> SectionType:
        if not value:
            return cls.UNKNOWN
        v = str(value).strip().upper()
        for member in cls:
            if member.value == v:
                return member
        return cls.UNKNOWN


class ValidationState(str, Enum):
    EXTRACTED = "EXTRACTED"
    CANDIDATE = "CANDIDATE"
    REVIEW = "REVIEW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RESOLVED = "RESOLVED"
    READY = "READY"
    AUTO_READY = "AUTO_READY"
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    VALIDATED_REFERENCE = "VALIDATED_REFERENCE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NOT_VALIDATED = "NOT_VALIDATED"

    @classmethod
    def from_str(cls, value: str | None) -> ValidationState:
        if not value:
            return cls.NOT_VALIDATED
        v = str(value).strip().upper()
        for member in cls:
            if member.value == v:
                return member
        return cls.NOT_VALIDATED


class SourceKind(str, Enum):
    ASSEMBLY_DXF = "ASSEMBLY_DXF"
    DESIGN_INPUT = "DESIGN_INPUT"
    MEMBER_SCHEDULE = "MEMBER_SCHEDULE"
    APPROVED_RULE = "APPROVED_RULE"
    DERIVED_GEOMETRY = "DERIVED_GEOMETRY"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REFERENCE_VALIDATION_ONLY = "REFERENCE_VALIDATION_ONLY"

    @classmethod
    def from_str(cls, value: str | None) -> SourceKind:
        if not value:
            return cls.DESIGN_INPUT
        v = str(value).strip().upper()
        for member in cls:
            if member.value == v:
                return member
        return cls.DESIGN_INPUT


class EndId(str, Enum):
    E1 = "E1"
    E2 = "E2"
    MID = "MID"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, value: str | None) -> EndId:
        if not value:
            return cls.UNKNOWN
        v = str(value).strip().upper()
        for member in cls:
            if member.value == v:
                return member
        return cls.UNKNOWN
