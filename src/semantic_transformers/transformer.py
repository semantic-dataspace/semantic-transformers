"""
Transformer: extractor output → OO-LD → RDF + DataFrame.

Usage
-----
    from semantic_transformers import Transformer
    from my_extractor import MyExtractor

    transformer = Transformer(
        extractor = MyExtractor(),
        transform = "simplified/transform.jsonata",
        context   = "specs/schema.oold.yaml",
    )
    result = transformer.run("my_file.csv")
    print(result.graph.serialize(format="turtle"))
    print(result.dataframe)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import rdflib
import yaml
from jsonata.jsonata import Jsonata

from .extractor import Extractor, ExtractionResult

# Namespaces used when generating timeseries descriptor triples.
_DCAT   = rdflib.Namespace("http://www.w3.org/ns/dcat#")
_QUDT   = rdflib.Namespace("http://qudt.org/schema/qudt/")
_OBI    = rdflib.Namespace("http://purl.obolibrary.org/obo/OBI_")
_RDFS   = rdflib.RDFS
_RDF    = rdflib.RDF


@dataclass
class TransformResult:
    """Everything produced by a single Transformer run."""

    # RDF graph containing the semantic metadata and timeseries descriptors.
    graph: rdflib.Dataset

    # The intermediate OO-LD document (after the JSONata transform, before RDF).
    oold_doc: dict

    # Raw measurement data.  None when the file had no tabular section.
    dataframe: object  # pd.DataFrame | None — avoid importing pandas at module level

    # Column name → ontology class IRI (same as ExtractionResult.column_iris).
    column_iris: dict[str, str]

    # Column name → QUDT unit IRI (same as ExtractionResult.column_units).
    column_units: dict[str, str]


class Transformer:
    """
    Connects a machine-specific Extractor to an OO-LD schema's transform and
    context, producing an RDF graph and a pandas DataFrame in one call.

    Parameters
    ----------
    extractor:
        Any object implementing the Extractor protocol.
    transform:
        Path to the schema's ``simplified/transform.jsonata`` file.
    context:
        Path to the schema's ``specs/schema.oold.yaml`` file.
    """

    def __init__(
        self,
        extractor: Extractor,
        transform: str | Path,
        context: str | Path,
    ) -> None:
        self.extractor      = extractor
        self._transform_src = Path(transform).read_text(encoding="utf-8")
        raw                 = yaml.safe_load(Path(context).read_text(encoding="utf-8"))
        self._context       = raw["@context"]
        # Derive the IRI base so we can construct timeseries node IRIs.
        self._base          = self._context.get("@base", "")

    # ------------------------------------------------------------------
    def run(self, file_path: str | Path, **overrides) -> TransformResult:
        """
        Process *file_path* end-to-end.

        Any keyword arguments (e.g. ``test_name``, ``specimen_iri``) are
        merged into the extracted simplified JSON, overriding whatever the
        extractor produced.  Use this to supply values that cannot be read
        from the file itself.

        Returns
        -------
        TransformResult
        """
        extraction = self.extractor.extract(Path(file_path))

        # Merge: extractor output first, then caller overrides.
        simplified = {**extraction.simplified_json, **overrides}

        # ── JSONata transform ──────────────────────────────────────────
        oold_doc = Jsonata(self._transform_src).evaluate(simplified)

        # ── OO-LD → RDF ───────────────────────────────────────────────
        g = rdflib.Dataset()
        g.parse(
            data   = json.dumps({"@context": self._context, **oold_doc}),
            format = "json-ld",
        )

        # ── Timeseries descriptor triples ─────────────────────────────
        if extraction.timeseries is not None and extraction.column_iris:
            test_iri = self._resolve_test_iri(g, oold_doc)
            if test_iri:
                self._add_timeseries_nodes(g, test_iri, extraction)

        return TransformResult(
            graph        = g,
            oold_doc     = oold_doc,
            dataframe    = extraction.timeseries,
            column_iris  = extraction.column_iris,
            column_units = extraction.column_units,
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
        # An absolute IRI in the OO-LD doc is used directly.
        if test_id.startswith("http"):
            return rdflib.URIRef(test_id)
        # Otherwise find the subject in the graph whose IRI ends with the id
        # string — this is the IRI that rdflib actually resolved via JSON-LD.
        for s, _p, _o, _c in g.quads():
            if isinstance(s, rdflib.URIRef) and str(s).endswith(test_id):
                return s
        return None

    def _add_timeseries_nodes(
        self,
        g: rdflib.Dataset,
        test_iri: rdflib.URIRef,
        extraction: ExtractionResult,
    ) -> None:
        """
        Add a dcat:Dataset node for the time series and one descriptor node
        per column.  Only IRIs and units go into the graph — not the values.

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

        for col_name, col_class in extraction.column_iris.items():
            safe    = col_name.replace(" ", "_")
            col_uri = rdflib.URIRef(str(ds_iri) + "/" + safe)

            ctx.add((ds_iri,    _DCAT.distribution, col_uri))
            ctx.add((col_uri,   _RDF.type,          rdflib.URIRef(col_class)))
            ctx.add((col_uri,   _RDFS.label,        rdflib.Literal(col_name)))

            unit_iri = extraction.column_units.get(col_name)
            if unit_iri:
                ctx.add((col_uri, _QUDT.hasUnit, rdflib.URIRef(unit_iri)))
