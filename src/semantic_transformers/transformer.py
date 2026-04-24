"""
Transformer: parser output → OO-LD → RDF + DataFrame.

Usage: shorthand (recommended)
--------------------------------
    from semantic_transformers import Transformer
    from semantic_transformers.parsers.characterization.tensile_test.testxpert_iii import TestXpertIIIParser

    # Pass the schema folder; all three file paths are derived automatically.
    # Works with a local path or a GitHub tree URL:
    transformer = Transformer(
        parser           = TestXpertIIIParser(),
        semantic_schema  = "https://github.com/org/semantic-schemas/tree/main/schemas/domain/Ontology/",
    )

    # Or for a locally cloned schema repository:
    transformer = Transformer(
        parser           = TestXpertIIIParser(),
        semantic_schema  = Path("../semantic-schemas/schemas/domain/Ontology/"),
    )

Usage: explicit paths (full control / non-standard layouts)
-------------------------------------------------------------
    transformer = Transformer(
        parser       = TestXpertIIIParser(),
        jsonata      = "specs/transform.simplified.jsonata",
        oold_schema  = "specs/schema.oold.yaml",
        input_schema = "specs/schema.simplified.json",  # optional
    )

    result = transformer.run("my_file.csv")
    print(result.graph.serialize(format="turtle"))
    print(result.dataframe)
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jsonschema
import rdflib
import yaml
from jsonata.jsonata import Jsonata

from .parser import Parser, ParseResult, SchemaAwareParser

# Namespaces used when generating timeseries descriptor triples.
_DCAT   = rdflib.Namespace("http://www.w3.org/ns/dcat#")
_QUDT   = rdflib.Namespace("http://qudt.org/schema/qudt/")
_OBI    = rdflib.Namespace("http://purl.obolibrary.org/obo/OBI_")
_RDFS   = rdflib.RDFS
_RDF    = rdflib.RDF

# Standard file paths relative to a schema folder root.
_JSONATA_REL     = "specs/transform.simplified.jsonata"
_OOLD_SCHEMA_REL = "specs/schema.oold.yaml"
_INPUT_SCHEMA_REL = "specs/schema.simplified.json"


def _read_text(source: str | Path) -> str:
    """Read text from a local file path or an HTTP(S) URL."""
    s = str(source)
    if s.startswith("http://") or s.startswith("https://"):
        with urllib.request.urlopen(s) as resp:
            return resp.read().decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


def _github_tree_to_raw(url: str) -> str:
    """
    Convert a GitHub ``tree/`` URL to a raw.githubusercontent.com base URL.

    Example
    -------
    https://github.com/org/repo/tree/main/schemas/domain/Ontology/
    → https://raw.githubusercontent.com/org/repo/main/schemas/domain/Ontology
    """
    url = url.rstrip("/")
    url = url.replace("https://github.com/", "https://raw.githubusercontent.com/", 1)
    url = re.sub(r"/tree/", "/", url, count=1)
    return url


def _resolve_semantic_schema(
    semantic_schema: str | Path,
) -> tuple[str | Path, str | Path, str | Path]:
    """
    Derive the three schema file locations from a folder root.

    Accepts either a local ``Path`` or a GitHub ``tree/`` URL string.
    Returns (jsonata, oold_schema, input_schema) as paths or URL strings.
    """
    s = str(semantic_schema)
    if s.startswith("http://") or s.startswith("https://"):
        base = _github_tree_to_raw(s)
        return (
            base + "/" + _JSONATA_REL,
            base + "/" + _OOLD_SCHEMA_REL,
            base + "/" + _INPUT_SCHEMA_REL,
        )
    p = Path(semantic_schema)
    return (
        p / _JSONATA_REL,
        p / _OOLD_SCHEMA_REL,
        p / _INPUT_SCHEMA_REL,
    )


@dataclass
class TransformResult:
    """Everything produced by a single Transformer run."""

    # RDF graph containing the semantic metadata and timeseries descriptors.
    graph: rdflib.Dataset

    # The intermediate OO-LD document (after the JSONata transform, before RDF).
    oold_doc: dict

    # Raw measurement data.  None when the file had no tabular section.
    dataframe: object  # pd.DataFrame | None (avoid importing pandas at module level)

    # Column name → ontology class IRI (same as ParseResult.column_iris).
    column_iris: dict[str, str]

    # Column name → QUDT unit IRI (same as ParseResult.column_units).
    column_units: dict[str, str]

    @property
    def flat_graph(self) -> rdflib.Graph:
        """
        Return a flat rdflib.Graph with all triples and namespace bindings
        from the internal Dataset.

        The Dataset is the internal representation; most downstream operations
        (serialisation, SPARQL, SHACL) work on a plain Graph.  Namespace
        bindings are copied so that prefixes defined in the schema @context
        (e.g. pmdco, tto, qudt) appear in serialised TTL output.
        """
        g = rdflib.Graph()
        for s, p, o, _ in self.graph.quads():
            g.add((s, p, o))
        for prefix, ns in self.graph.namespaces():
            g.bind(prefix, ns)
        return g


class Transformer:
    """
    Connects a machine-specific Parser to an OO-LD schema, producing an RDF
    graph and a pandas DataFrame in one call.

    Parameters
    ----------
    parser:
        Any object implementing the Parser protocol.

    semantic_schema:
        Shorthand: the root folder of the schema, either a local ``Path``
        or a GitHub ``tree/`` URL.  Derives all three file paths using the
        standard schema folder layout.  Any explicitly provided ``jsonata``,
        ``oold_schema``, or ``input_schema`` value takes precedence over the
        derived path.

    jsonata:
        Path or URL to the schema's ``specs/transform.simplified.jsonata`` file.

    oold_schema:
        Path or URL to the schema's ``specs/schema.oold.yaml`` file (contains
        the JSON-LD ``@context`` used to convert OO-LD output to RDF).

    input_schema:
        Optional path or URL to the schema's ``specs/schema.simplified.json``
        file.  When provided, the parser's output (after caller overrides) is
        validated for type correctness before being passed to the JSONata
        transform.  Catches field-name mismatches between a parser and its
        target schema early.  Required-field completeness is intentionally not
        enforced here; SHACL validation handles that downstream.

    Examples
    --------
    Shorthand with a GitHub URL (no local clone needed)::

        transformer = Transformer(
            parser          = ZwickParser(),
            semantic_schema = "https://github.com/org/semantic-schemas/tree/main/schemas/domain/Ontology/",
        )

    Shorthand with a local path::

        transformer = Transformer(
            parser          = ZwickParser(),
            semantic_schema = Path("../semantic-schemas/schemas/domain/Ontology/"),
        )

    Explicit paths (non-standard layout, or to override one file)::

        transformer = Transformer(
            parser       = ZwickParser(),
            jsonata      = "specs/transform.simplified.jsonata",
            oold_schema  = "specs/schema.oold.yaml",
            input_schema = "specs/schema.simplified.json",
        )
    """

    def __init__(
        self,
        parser: Parser,
        jsonata: Optional[str | Path] = None,
        oold_schema: Optional[str | Path] = None,
        input_schema: Optional[str | Path] = None,
        *,
        semantic_schema: Optional[str | Path] = None,
    ) -> None:
        self.parser = parser

        # Resolve shorthand, then let explicit values override.
        if semantic_schema is not None:
            derived_jsonata, derived_oold, derived_input = _resolve_semantic_schema(semantic_schema)
            jsonata      = jsonata      or derived_jsonata
            oold_schema  = oold_schema  or derived_oold
            input_schema = input_schema or derived_input

        if jsonata is None:
            raise ValueError(
                "Provide either 'semantic_schema' (shorthand) or 'jsonata' explicitly."
            )
        if oold_schema is None:
            raise ValueError(
                "Provide either 'semantic_schema' (shorthand) or 'oold_schema' explicitly."
            )

        self._transform_src = _read_text(jsonata)
        raw = yaml.safe_load(_read_text(oold_schema))
        self._context = raw["@context"]
        self._base    = self._context.get("@base", "")

        self._input_schema: dict | None = (
            json.loads(_read_text(input_schema))
            if input_schema is not None
            else None
        )

        # Share the loaded schema with the parser if it supports it.
        if self._input_schema is not None and isinstance(parser, SchemaAwareParser):
            parser.configure(self._input_schema)

    # ------------------------------------------------------------------
    def run(
        self,
        file_path: str | Path,
        *,
        base: Optional[str] = None,
        **overrides,
    ) -> TransformResult:
        """
        Process *file_path* end-to-end.

        Parameters
        ----------
        file_path :
            Path to the instrument export file.
        base :
            Base IRI used to resolve relative node identifiers in the
            OO-LD document.  Overrides the ``@base`` entry in the schema
            context.  Relative IDs such as ``tensile-test-abc`` are resolved
            against this IRI (e.g. ``https://example.org/`` produces
            ``https://example.org/tensile-test-abc``).

            When omitted, the schema's own ``@base`` is used.  Most schema
            contexts set ``@base`` to a namespace under ``w3id.org/pmd/co/``
            which is intended for ontology terms, not data instances.  Pass
            your own IRI to produce portable, globally-unique data IRIs.
        **overrides :
            Any additional keyword arguments (e.g. ``test_name``,
            ``specimen_iri``) are merged into the parsed simplified JSON,
            overriding whatever the parser produced.

        Returns
        -------
        TransformResult
        """
        parsed = self.parser.parse(Path(file_path))

        # Merge: parser output first, then caller overrides.
        simplified = {**parsed.simplified_json, **overrides}

        # ── Validate against input schema (if provided) ───────────────
        # Strip 'required' before validating: fields that cannot be parsed
        # from the file (e.g. specimen_iri, which must be supplied by the
        # caller) are legitimately absent here.  The goal is to catch type
        # mismatches and unknown field names, not to enforce completeness —
        # SHACL validation downstream will flag any missing required triples.
        if self._input_schema is not None:
            schema_for_validation = {**self._input_schema, "required": []}
            jsonschema.validate(instance=simplified, schema=schema_for_validation)

        # ── JSONata transform ──────────────────────────────────────────
        oold_doc = Jsonata(self._transform_src).evaluate(simplified)

        # ── OO-LD → RDF ───────────────────────────────────────────────
        ctx = {**self._context, **({} if base is None else {"@base": base})}
        g = rdflib.Dataset()
        g.parse(
            data   = json.dumps({"@context": ctx, **oold_doc}),
            format = "json-ld",
        )

        # ── Timeseries descriptor triples ─────────────────────────────
        if parsed.timeseries is not None and parsed.column_iris:
            test_iri = self._resolve_test_iri(g, oold_doc)
            if test_iri:
                self._add_timeseries_nodes(g, test_iri, parsed)

        return TransformResult(
            graph        = g,
            oold_doc     = oold_doc,
            dataframe    = parsed.timeseries,
            column_iris  = parsed.column_iris,
            column_units = parsed.column_units,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_test_iri(self, g: rdflib.Dataset, oold_doc: dict) -> rdflib.URIRef | None:
        """
        Find the root test node's IRI in the parsed graph.

        We look it up rather than constructing it from ``@base + id`` because
        JSON-LD follows RFC 3986 which strips any fragment from the base URI
        before resolving relative references.  Naive string concatenation would
        therefore produce the wrong IRI when the schema context uses a
        ``@base`` that ends with ``#``.
        """
        test_id = oold_doc.get("id", "")
        if not test_id:
            return None
        if test_id.startswith("http"):
            return rdflib.URIRef(test_id)
        for s, _p, _o, _c in g.quads():
            if isinstance(s, rdflib.URIRef) and str(s).endswith(test_id):
                return s
        return None

    def _add_timeseries_nodes(
        self,
        g: rdflib.Dataset,
        test_iri: rdflib.URIRef,
        parsed: ParseResult,
    ) -> None:
        """
        Add a dcat:Dataset node for the time series and one descriptor node
        per column.  Only IRIs and units go into the graph (not the values).

        Graph pattern added
        -------------------
            <test_iri>  obi:has_specified_output  <test_iri/timeseries> .

            <test_iri/timeseries>
                a               dcat:Dataset ;
                rdfs:label      "Raw time series" ;
                dcat:distribution  <test_iri/timeseries/ColumnName>, ... .

            <test_iri/timeseries/ColumnName>
                a               <column_class_iri> ;
                rdfs:label      "ColumnName" ;
                qudt:hasUnit    <unit_iri> .
        """
        ctx = g.default_graph

        ds_iri = rdflib.URIRef(str(test_iri) + "/timeseries")

        ctx.add((test_iri, _OBI["0000299"], ds_iri))   # has_specified_output
        ctx.add((ds_iri, _RDF.type,   _DCAT.Dataset))
        ctx.add((ds_iri, _RDFS.label, rdflib.Literal("Raw time series")))

        for col_name, col_class in parsed.column_iris.items():
            safe    = col_name.replace(" ", "_")
            col_uri = rdflib.URIRef(str(ds_iri) + "/" + safe)

            ctx.add((ds_iri,    _DCAT.distribution, col_uri))
            ctx.add((col_uri,   _RDF.type,          rdflib.URIRef(col_class)))
            ctx.add((col_uri,   _RDFS.label,        rdflib.Literal(col_name)))

            unit_iri = parsed.column_units.get(col_name)
            if unit_iri:
                ctx.add((col_uri, _QUDT.hasUnit, rdflib.URIRef(unit_iri)))
