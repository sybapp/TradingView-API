"""Reader for the Versioned Dataset Contract produced by the JS collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Union


JsonObject = Dict[str, Any]


@dataclass(frozen=True)
class DatasetBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class FeatureRecord:
    id: str
    source: str
    indicator_id: str
    type: str
    name: str
    event_time: datetime
    availability_time: datetime
    repainting_risk: str
    value: Any


@dataclass(frozen=True)
class VersionedDataset:
    manifest: JsonObject
    bars: List[DatasetBar]
    features: List[FeatureRecord]
    path: Path

    @property
    def dataset_id(self) -> str:
        return str(self.manifest["datasetId"])

    @property
    def price_scale(self) -> int:
        return int(self.manifest["bar"]["priceScale"])

    @property
    def interval(self) -> str:
        return str(self.manifest["bar"]["interval"])

    @property
    def ticker(self) -> str:
        return str(self.manifest["symbol"]["ticker"])


def load_versioned_dataset(dataset_path: Union[str, Path]) -> VersionedDataset:
    path = Path(dataset_path)
    manifest = _read_json_object(path / "manifest.json")
    bars = [_parse_bar(bar) for bar in _read_json_array(path / "bars.json")]
    features = [_parse_feature(feature) for feature in _read_json_array(path / "features.json")]

    return VersionedDataset(
        manifest=manifest,
        bars=bars,
        features=features,
        path=path,
    )


def _read_json_object(path: Path) -> JsonObject:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_json_array(path: Path) -> List[JsonObject]:
    value = _read_json(path)
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON array")
    return value


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _parse_bar(record: JsonObject) -> DatasetBar:
    return DatasetBar(
        time=_parse_timestamp(record["time"]),
        open=float(record["open"]),
        high=float(record["high"]),
        low=float(record["low"]),
        close=float(record["close"]),
        volume=int(record["volume"]),
    )


def _parse_feature(record: JsonObject) -> FeatureRecord:
    return FeatureRecord(
        id=str(record["id"]),
        source=str(record["source"]),
        indicator_id=str(record["indicatorId"]),
        type=str(record["type"]),
        name=str(record["name"]),
        event_time=_parse_timestamp(record["eventTime"]),
        availability_time=_parse_timestamp(record["availabilityTime"]),
        repainting_risk=str(record["repaintingRisk"]),
        value=record["value"],
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
