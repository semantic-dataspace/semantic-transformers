"""testXpert III parser pre-configured for German-locale exports."""

from pathlib import Path
from typing import Optional

from ..parser import TestXpertIIIParser as _Base, _DEFAULT_METADATA_ROWS

_COLUMN_MAPPING = Path(__file__).parent / "column_mapping.json"

_META_FIELD_MAP: dict[str, str] = {
    "Prüfnorm":              "test_standard",
    "Temperatur":            "temperature",
    "Prüfgeschwindigkeit":   "strain_rate",
    "Messlänge Standardweg": "gauge_length",
    "Vorkraft":              "preload",
}

_UNIT_FIELD_MAP: dict[str, tuple[str, str]] = {
    "Prüfgeschwindigkeit":   ("strain_rate_unit",  "mm/s"),
    "Messlänge Standardweg": ("gauge_length_unit", "mm"),
    "Vorkraft":              ("preload_unit",       "MPa"),
}

_STRAIN_RATE_LABEL = "Prüfgeschwindigkeit"
_DATE_LABEL        = "Datum/Uhrzeit"


class TestXpertIIIParser(_Base):
    """
    TestXpertIIIParser pre-configured for German-locale testXpert III exports.

    All constructor parameters are optional; German label strings and
    ``column_mapping.json`` are used by default.

    Parameters
    ----------
    strain_rate_label:
        Deprecated.  Use *unit_field_map* instead.  Pass ``None`` to suppress
        ``strain_rate_unit`` extraction.
    """

    def __init__(
        self,
        column_mapping_path: Optional[Path] = None,
        *,
        metadata_rows: int = _DEFAULT_METADATA_ROWS,
        meta_field_map: Optional[dict[str, str]] = None,
        unit_field_map: Optional[dict[str, tuple[str, str]]] = None,
        date_label: Optional[str] = _DATE_LABEL,
        strain_rate_label: Optional[str] = _STRAIN_RATE_LABEL,
    ) -> None:
        if unit_field_map is None:
            resolved_units = dict(_UNIT_FIELD_MAP)
            if strain_rate_label and strain_rate_label != _STRAIN_RATE_LABEL:
                resolved_units[strain_rate_label] = ("strain_rate_unit", "mm/s")
            elif strain_rate_label is None:
                resolved_units.pop(_STRAIN_RATE_LABEL, None)
        else:
            resolved_units = unit_field_map

        super().__init__(
            column_mapping_path if column_mapping_path is not None else _COLUMN_MAPPING,
            metadata_rows = metadata_rows,
            meta_field_map = meta_field_map if meta_field_map is not None else _META_FIELD_MAP,
            unit_field_map = resolved_units,
            date_label     = date_label,
        )


__all__ = ["TestXpertIIIParser"]
