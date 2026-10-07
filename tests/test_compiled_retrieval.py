from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.compiled_retrieval import CompiledLibrary, CompiledRetrievalError


def library_payload() -> dict:
    """Invented definitions, unrelated to the owner's papers and benchmark."""
    return {
        "nodes": {
            "map-continuous": {
                "id": "map-continuous",
                "name": "Bounded map",
                "kind": "definition",
                "layer": "definitions",
                "paper": "source-analysis",
                "statement": "A continuous linear map between two normed spaces.",
                "conditions": ["Both spaces are normed.", "The map is linear."],
                "formal": "For some M, ||Tx|| <= M ||x|| for all x.",
                "inputs_outputs": "A vector is mapped to a vector.",
                "depends_on": [{"target": "norm", "role": "definition", "note": "Fix the norm first."}],
                "relations": [{"target": "map-finite-image", "type": "differs-from", "evidence": "The second definition describes the image."}],
                "distinguish_from": [{"term": "Bounded map", "here": "The operator norm is finite.", "not": "The whole image must be bounded."}],
                "evidence": [{"section": "Definition", "quote": "Continuity is characterized by this inequality."}],
                "epistemic": {"status": "reviewed", "note": "These assumptions define the scope."},
                "surfaces": {
                    "gloss": "Continuity criterion for a linear operator",
                    "sense_key": "bounded linear operator",
                    "query_forms": ["When is a linear transformation continuous?"],
                    "zh": ["有界线性算子如何判定连续？"],
                    "symbols": ["||T||"],
                    "not_this": ["A map whose whole image fits inside a ball"],
                },
            },
            "map-finite-image": {
                "name": "Bounded map",
                "kind": "definition",
                "layer": "definitions",
                "paper": "source-image",
                "statement": "The image of the entire domain is a bounded subset.",
                "conditions": ["The codomain is a metric space."],
                "surfaces": {"gloss": "A globally bounded image"},
            },
            "norm": {
                "name": "Norm",
                "layer": "definitions",
                "statement": "A homogeneous positive definite function satisfying the triangle inequality.",
                "notation": [{"symbol": "||x||", "meaning": "Length of x"}, "norm notation", ["x", "vector"]],
            },
        },
        "papers": {
            "source-analysis": {"title": "Notes on linear maps", "year": 2000, "short": ["Linear maps"], "zh": ["线性映射笔记"]},
            "source-image": {"title": "Notes on bounded images", "year": 2001},
        },
        "terms": {
            "bounded map": {
                "term": "Bounded map",
                "disambiguation": ["Specify whether boundedness describes the operator or its full image."],
                "senses": [
                    {"id": "map-continuous", "gloss": "A bounded operator", "context": "Linear analysis"},
                    {"id": "map-finite-image", "gloss": "A bounded full image", "context": "Metric maps"},
                ],
            },
        },
        "edges": [{"source": "map-continuous", "target": "map-finite-image", "type": "contrasts-with", "evidence": "They have different quantifiers."}],
        "claims": [{
            "question": "Does boundedness imply continuity?",
            "relation": "scope-dependent",
            "note": "The statement changes with the definition.",
            "positions": [
                {"id": "map-continuous", "year": 2000, "position": "It implies continuity for linear maps."},
                {"id": "map-finite-image", "year": 2001, "position": "A bounded image does not require continuity."},
            ],
            "timeline": ["map-continuous", "map-finite-image"],
        }],
    }


def payload_bytes(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class CompiledLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = library_payload()
        self.library = CompiledLibrary.from_payload(self.payload)

    def test_search_indexes_complete_definitions_and_authored_surfaces(self) -> None:
        self.assertEqual("map-continuous", self.library.search("有界线性算子如何判定连续", 1)[0]["reference"])
        self.assertEqual("norm", self.library.search("homogeneous positive definite", 1)[0]["reference"])
        payload = {"nodes": {"long": {"name": "Long definition", "statement": "prefix " * 300 + "unabridged_tail"}}}
        result = CompiledLibrary.from_payload(payload).search("unabridged_tail")
        self.assertEqual(["long"], [row["reference"] for row in result])

    def test_metadata_never_enters_semantic_text_or_search(self) -> None:
        payload = {
            "nodes": {"OpaqueLookupAlpha": {
                "id": "OpaqueLookupAlpha", "name": "Actual concept", "statement": "Actual meaning",
                "paper": "OpaqueSourceBeta", "sha256": "SecretHashGamma", "path": "PrivatePathDelta",
                "surfaces": {"id": "SurfaceIdentityEpsilon", "gloss": "A useful gloss"},
            }},
            "papers": {"OpaqueSourceBeta": {"title": "Readable source", "key": "SourceKeyZeta", "arxiv": "ArxivEta", "path": "SourcePathTheta"}},
        }
        library = CompiledLibrary.from_payload(payload)
        for text in ["OpaqueLookupAlpha", "OpaqueSourceBeta", "SecretHashGamma", "PrivatePathDelta", "SurfaceIdentityEpsilon", "SourceKeyZeta", "ArxivEta", "SourcePathTheta"]:
            with self.subTest(text=text):
                self.assertNotIn(text, library.semantic_text("OpaqueLookupAlpha"))
                self.assertEqual([], library.search(text))
        self.assertIn("Readable source", library.semantic_text("OpaqueLookupAlpha"))

    def test_lookup_does_not_merge_homonyms_or_accept_search_names_as_identity(self) -> None:
        rows = self.library.search("Bounded map", 10)
        refs = {row["reference"] for row in rows}
        self.assertTrue({"map-continuous", "map-finite-image"}.issubset(refs))
        self.assertNotEqual(self.library.get("map-continuous")["statement"], self.library.get("map-finite-image")["statement"])
        with self.assertRaisesRegex(CompiledRetrievalError, "unknown knowledge reference"):
            self.library.get("Bounded map")
        self.assertFalse(any("identity_authority" in row for row in rows))

    def test_explicit_dependencies_claims_timeline_and_incoming_edges_survive(self) -> None:
        entry = self.library.get("map-continuous")
        self.assertEqual(self.payload["nodes"]["map-continuous"]["formal"], entry["formal"])
        self.assertEqual(self.payload["nodes"]["map-continuous"]["evidence"], entry["evidence"])
        self.assertEqual(self.payload["nodes"]["map-continuous"]["epistemic"], entry["epistemic"])
        self.assertEqual(self.payload["nodes"]["map-continuous"]["distinguish_from"], entry["distinguish_from"])
        self.assertEqual("Norm", entry["depends_on"][0]["name"])
        self.assertEqual("Fix the norm first.", entry["depends_on"][0]["note"])
        self.assertEqual("differs-from", entry["relations"][0]["type"])
        claim = entry["claims"][0]
        self.assertEqual([2000, 2001], [row["source"]["year"] for row in claim["timeline"]])
        self.assertEqual("A bounded image does not require continuity.", claim["positions"][1]["position"])
        self.assertEqual("in", self.library.get("map-finite-image")["edges"][0]["direction"])
        self.assertEqual(3, len(self.library.get("norm")["notation"]))

    def test_source_search_surfaces_do_not_bloat_entry_source_metadata(self) -> None:
        self.assertEqual("map-continuous", self.library.search("线性映射笔记", 1)[0]["reference"])
        self.assertEqual({"title": "Notes on linear maps", "year": 2000}, self.library.get("map-continuous")["source"])

    def test_scientific_json_qualifications_survive_get_index_and_pack(self) -> None:
        payload = library_payload()
        node = payload["nodes"]["map-continuous"]
        evidence = {"section": "Support", "quote": "An authored support statement.",
                    "source_title": "Support source title", "conditions": ["EvidenceDomainRestriction"],
                    "units": {"scale": "AuthoredUnitConvention"},
                    "qualifiers": {"id": "ScientificIdentity", "path": "ScientificTrajectory", "hash": "ScientificHashFamily",
                                   "reference": "ScientificReferenceQuantity", "available": False,
                                   "provenance": "ScientificProvenanceStatement"}}
        node["evidence"] = [evidence]
        node["epistemic"] = {"status": "provisional", "note": "Review is pending.",
                             "conditions": ["EpistemicApplicability"], "confidence_interval": [0.1, 0.4]}
        node["notation"] = [{"symbol": "x", "meaning": "An input", "units": "NotationUnits", "domain": {"finite": False}}]
        library = CompiledLibrary.from_payload(payload)
        entry = library.get("map-continuous")
        packet = library.pack(["map-continuous"], 20000)["entries"][0]
        for field in ("evidence", "epistemic", "notation"):
            self.assertEqual(node[field], entry[field])
            self.assertEqual(node[field], packet[field])
        for text in ("Support source title", "EvidenceDomainRestriction", "AuthoredUnitConvention", "ScientificIdentity",
                     "ScientificTrajectory", "ScientificHashFamily", "ScientificReferenceQuantity", "ScientificProvenanceStatement",
                     "EpistemicApplicability", "NotationUnits"):
            self.assertIn(text, library.semantic_text("map-continuous"))
        self.assertEqual([], entry["gaps"])
        entry["evidence"][0]["conditions"].clear()
        payload["nodes"]["map-continuous"]["evidence"][0]["units"].clear()
        self.assertEqual(["EvidenceDomainRestriction"], library.get("map-continuous")["evidence"][0]["conditions"])

    def test_authored_relationship_sources_conditions_and_units_stay_at_their_scope(self) -> None:
        payload = library_payload()
        relation = payload["nodes"]["map-continuous"]["relations"][0]
        relation.update(source={"title": "Authored relation source"}, year=2012,
                        conditions=["RelationDomainQualifier"], units={"norm": "RelationUnits"},
                        qualifiers={"scope": "RelationSupportScope"})
        dependency = payload["nodes"]["map-continuous"]["depends_on"][0]
        dependency["conditions"] = ["DependencySpecificCondition"]
        library = CompiledLibrary.from_payload(payload)
        entry = library.get("map-continuous")
        packet = library.pack(["map-continuous", "norm"], 20000)["entries"][0]
        self.assertEqual("map-finite-image", packet["relations"][0]["reference"])
        for key in ("source", "year", "conditions", "units", "qualifiers"):
            self.assertEqual(relation[key], entry["relations"][0][key])
            self.assertEqual(relation[key], packet["relations"][0][key])
        self.assertEqual(["DependencySpecificCondition"], packet["depends_on"][0]["conditions"])
        self.assertNotIn("source", packet["depends_on"][0])
        for text in ("Authored relation source", "RelationDomainQualifier", "RelationUnits", "RelationSupportScope", "DependencySpecificCondition"):
            self.assertIn(text, library.semantic_text("map-continuous"))

    def test_acquisition_metadata_is_preserved_but_does_not_enter_semantic_index(self) -> None:
        payload = library_payload()
        provenance = {"file": "AcquisitionPathSentinel", "sha256": "AcquisitionHashSentinel"}
        payload["nodes"]["map-continuous"]["evidence"][0]["provenance"] = provenance
        payload["nodes"]["map-continuous"]["evidence"][0]["qualifiers"] = {"provenance": "ScientificProvenanceSentinel"}
        payload["edges"][0]["origin"] = "AcquisitionOriginSentinel"
        library = CompiledLibrary.from_payload(payload)
        entry = library.get("map-continuous")
        packet = library.pack(["map-continuous", "map-finite-image"], 20000)
        self.assertEqual(provenance, entry["evidence"][0]["provenance"])
        self.assertEqual(provenance, packet["entries"][0]["evidence"][0]["provenance"])
        self.assertEqual("AcquisitionOriginSentinel", entry["edges"][0]["origin"])
        self.assertNotIn("origin", packet["edges"][0])
        for text in ("AcquisitionPathSentinel", "AcquisitionHashSentinel", "AcquisitionOriginSentinel"):
            self.assertNotIn(text, library.semantic_text("map-continuous"))
            self.assertEqual([], library.search(text))
        self.assertIn("ScientificProvenanceSentinel", library.semantic_text("map-continuous"))

    def test_scientific_claim_and_edge_sharing_distinguishes_booleans_from_numbers(self) -> None:
        payload = library_payload()
        claim, edge = copy.deepcopy(payload["claims"][0]), copy.deepcopy(payload["edges"][0])
        payload["claims"] = [{**copy.deepcopy(claim), "qualifiers": {"annotation": value}} for value in (False, 0, 1, 1.0)]
        payload["edges"] = [{**copy.deepcopy(edge), "qualifiers": {"annotation": value},
                              "conditions": ["EdgeApplicability"]} for value in (False, 0, 1, 1.0)]
        library = CompiledLibrary.from_payload(payload)
        packet = library.pack(["map-continuous", "map-finite-image"], 20000)
        self.assertEqual(4, len(library.get("map-continuous")["claims"]))
        self.assertEqual(3, len(packet["claims"]))
        self.assertEqual(3, len(packet["edges"]))
        self.assertEqual(3, len(library.browse("definitions")["claims"]))
        self.assertIs(False, packet["claims"][0]["qualifiers"]["annotation"])
        self.assertIs(type(packet["claims"][1]["qualifiers"]["annotation"]), int)
        self.assertEqual(["EdgeApplicability"], packet["edges"][0]["conditions"])

    def test_invalid_json_scientific_attributes_and_existing_field_types_fail_explicitly(self) -> None:
        for value in ({"not-a-json-set"}, ("not-a-json-array",), b"not-json-bytes", float("nan"), float("inf"), {1: "not-a-string-key"}):
            payload = library_payload()
            payload["nodes"]["map-continuous"]["evidence"][0]["qualifiers"] = value
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(CompiledRetrievalError, "evidence|JSON|string keys"):
                CompiledLibrary.from_payload(payload)
        payload = library_payload()
        payload["nodes"]["map-continuous"]["evidence"][0]["quote"] = 7
        with self.assertRaisesRegex(CompiledRetrievalError, "quote must be text"):
            CompiledLibrary.from_payload(payload)

    def test_unresolved_references_stay_gaps_without_name_inference(self) -> None:
        payload = library_payload()
        payload["nodes"]["map-continuous"]["depends_on"].append({"target": "Norm", "note": "Unresolved authored target"})
        library = CompiledLibrary.from_payload(payload)
        entry = library.get("map-continuous")
        missing = entry["depends_on"][1]
        self.assertFalse(missing["available"])
        self.assertNotIn("name", missing)
        self.assertIn({"reference": "Norm", "reason": "unresolved-reference"}, entry["gaps"])

    def test_overview_and_browse_follow_authored_sources_and_sense_groups(self) -> None:
        root = self.library.overview()
        self.assertEqual(["Notes on linear maps", "Notes on bounded images"], [source["title"] for source in root["sources"]])
        source = self.library.browse("source-analysis")
        self.assertEqual(["map-continuous"], [entry["reference"] for entry in source["entries"]])
        self.assertEqual("Norm", source["entries"][0]["depends_on"][0]["name"])
        self.assertEqual(1, len(source["claims"]))
        senses = self.library.tree("bounded map")
        self.assertEqual(["map-continuous", "map-finite-image"], [sense["reference"] for sense in senses["senses"]])
        self.assertEqual(root, self.library.tree())
        self.assertEqual(3, len(self.library.browse("definitions")["entries"]))

    def test_inventory_keeps_senses_and_exact_uses_separate_with_full_entries(self) -> None:
        payload = library_payload()
        payload["nodes"]["map-continuous"]["surfaces"]["head_terms"] = ["Bounded map"]
        payload["nodes"]["map-use"] = {
            "name": "An application", "paper": "source-image",
            "statement": "An argument uses bounded maps under explicit conditions.",
            "conditions": ["Use the declared metric."], "surfaces": {"head_terms": ["Bounded map"]},
        }
        payload["nodes"]["name-only"] = {
            "name": "Bounded map", "aliases": ["Bounded map"],
            "statement": "Bounded map", "surfaces": {"query_forms": ["Bounded map"], "zh": ["Bounded map"]},
        }
        library = CompiledLibrary.from_payload(payload)
        inventory = library.inventory("Bounded map")
        self.assertTrue(inventory["matched"])
        self.assertEqual("compiled declarations", inventory["scope"])
        self.assertEqual("not-certified", inventory["source_corpus_completeness"])
        self.assertEqual(["map-continuous", "map-finite-image"], [row["reference"] for row in inventory["groups"][0]["senses"]])
        self.assertEqual(["map-continuous", "map-use"], [row["reference"] for row in inventory["uses"]])
        for row in inventory["groups"][0]["senses"] + inventory["uses"]:
            self.assertTrue(row["available"])
            for field, value in library.get(row["reference"]).items():
                self.assertEqual(value, row[field], field)
        self.assertNotEqual(inventory["groups"][0]["senses"][0]["statement"], inventory["groups"][0]["senses"][1]["statement"])
        self.assertEqual(payload["terms"]["bounded map"]["disambiguation"], inventory["groups"][0]["disambiguation"])
        self.assertEqual("Linear analysis", inventory["groups"][0]["senses"][0]["declaration"]["context"])
        self.assertNotIn("meaning_count", inventory)

    def test_inventory_normalizes_whole_declared_names_without_merging_groups(self) -> None:
        payload = library_payload()
        payload["terms"]["another handle"] = {
            "term": "Ｂｏｕｎｄｅｄ　ｍａｐ", "senses": [{"id": "map-finite-image"}],
        }
        payload["nodes"]["map-continuous"]["surfaces"]["head_terms"] = ["ＢＯＵＮＤＥＤ\t MAP"]
        payload["nodes"]["map-finite-image"]["surfaces"]["head_terms"] = ["Bounded maps"]
        library = CompiledLibrary.from_payload(payload)
        inventory = library.inventory("  bounded\nmap  ")
        self.assertEqual(["bounded map", "another handle"], [group["reference"] for group in inventory["groups"]])
        self.assertEqual(["map-continuous"], [row["reference"] for row in inventory["uses"]])
        self.assertEqual(["map-finite-image"], [row["reference"] for row in library.inventory("another handle")["groups"][0]["senses"]])
        for name in ("boundedmap", "bounded mapper", "map-continuous", "Norm"):
            with self.subTest(name=name):
                result = library.inventory(name)
                self.assertFalse(result["matched"])
                self.assertEqual([], result["groups"])
                self.assertEqual([], result["uses"])
                self.assertEqual([{"term": name, "reason": "unmatched-term"}], result["gaps"])

    def test_inventory_has_no_top_k_and_retains_unregistered_tail_declarations(self) -> None:
        references = [f"entry-{index}" for index in range(600)]
        payload = {
            "nodes": {reference: {"name": "Shared heading", "statement": reference,
                                  "surfaces": {"head_terms": ["Shared heading"]}} for reference in references},
            "terms": {"Shared heading": {"term": "Shared heading", "senses": [
                *[{"id": reference} for reference in references],
                {"id": "unregistered-tail", "gloss": "A declared missing member", "context": "Last declaration"},
            ]}},
        }
        library = CompiledLibrary.from_payload(payload)
        with patch.object(library, "search", side_effect=AssertionError("inventory must not rank")):
            result = library.inventory("Shared heading")
        self.assertEqual(references, [row["reference"] for row in result["uses"]])
        self.assertEqual(references + ["unregistered-tail"], [row["reference"] for row in result["groups"][0]["senses"]])
        tail = result["groups"][0]["senses"][-1]
        self.assertFalse(tail["available"])
        self.assertEqual("Last declaration", tail["declaration"]["context"])
        self.assertIn({"reference": "unregistered-tail", "reason": "unresolved-reference"}, result["gaps"])

    def test_inventory_aggregates_missing_sources_and_dependencies_without_guessing(self) -> None:
        payload = library_payload()
        payload["nodes"]["map-continuous"]["paper"] = "unregistered-source"
        payload["nodes"]["map-continuous"]["depends_on"].append({"target": "unregistered-premise"})
        payload["nodes"]["map-continuous"]["surfaces"]["head_terms"] = ["Bounded map"]
        payload["terms"]["bounded map"]["senses"].append({"id": "Bounded map", "gloss": "This is an unregistered reference"})
        result = CompiledLibrary.from_payload(payload).inventory("Bounded map")
        self.assertFalse(result["groups"][0]["senses"][-1]["available"])
        for gap in (
            {"reference": "unregistered-source", "reason": "unresolved-source"},
            {"reference": "unregistered-premise", "reason": "unresolved-reference"},
            {"reference": "Bounded map", "reason": "unresolved-reference"},
        ):
            self.assertEqual(1, result["gaps"].count(gap))

    def test_inventory_input_and_result_are_detached_and_invalid_terms_are_explicit(self) -> None:
        payload = library_payload()
        payload["nodes"]["map-continuous"]["surfaces"]["head_terms"] = ["Bounded map"]
        library = CompiledLibrary.from_payload(payload)
        expected = library.inventory("Bounded map")
        changed = library.inventory("Bounded map")
        changed["groups"][0]["disambiguation"].clear()
        changed["groups"][0]["senses"][0]["statement"] = "Invented replacement"
        changed["uses"][0]["conditions"].clear()
        changed["gaps"].append({"reason": "invented gap"})
        payload["terms"]["bounded map"]["senses"].clear()
        payload["nodes"]["map-continuous"]["conditions"].clear()
        self.assertEqual(expected, library.inventory("Bounded map"))
        for invalid in (None, False, 1, [], {}, "", " \t\u3000"):
            with self.subTest(term=invalid), self.assertRaisesRegex(CompiledRetrievalError, "nonempty text"):
                library.inventory(invalid)

    def test_navigation_never_guesses_between_colliding_handles(self) -> None:
        payload = library_payload()
        payload["terms"]["source-analysis"] = payload["terms"].pop("bounded map")
        library = CompiledLibrary.from_payload(payload)
        with self.assertRaisesRegex(CompiledRetrievalError, "ambiguous navigation"):
            library.browse("source-analysis")
        self.assertEqual("Notes on linear maps", library.browse("source-analysis", kind="source")["title"])
        self.assertEqual("Bounded map", library.browse("source-analysis", kind="term")["term"])

    def test_input_and_return_values_are_detached(self) -> None:
        original = copy.deepcopy(self.payload)
        entry = self.library.get("map-continuous")
        entry["conditions"].clear()
        self.payload["nodes"]["map-continuous"]["statement"] = "changed"
        self.assertEqual(original["nodes"]["map-continuous"]["statement"], self.library.get("map-continuous")["statement"])
        self.assertEqual(2, len(self.library.get("map-continuous")["conditions"]))

    def test_pack_keeps_scientific_fields_and_counts_utf8_payload_bytes(self) -> None:
        result = self.library.pack(["map-continuous", "norm"], 20000)
        first = result["entries"][0]
        complete = self.library.get("map-continuous")
        for field in ("statement", "conditions", "formal", "inputs_outputs", "notation", "distinguish_from", "evidence", "epistemic"):
            self.assertEqual(complete[field], first[field], field)
        self.assertEqual(complete["depends_on"][0]["note"], first["depends_on"][0]["note"])
        self.assertEqual(complete["relations"][0]["evidence"], first["relations"][0]["evidence"])
        self.assertEqual(complete["relations"][0]["reference"], first["relations"][0]["reference"])
        self.assertEqual(complete["surfaces"]["sense_key"], first["sense"])
        self.assertEqual(complete["surfaces"]["not_this"], first["not_this"])
        self.assertNotIn("surfaces", first)
        self.assertNotIn("claims", first)
        self.assertNotIn("edges", first)
        self.assertEqual([], result["claims"])
        self.assertEqual([], result["edges"])
        self.assertEqual(complete["claims"], self.library.get("map-continuous")["claims"])
        self.assertEqual(complete["surfaces"], self.library.get("map-continuous")["surfaces"])
        self.assertEqual([], result["gaps"])
        self.assertEqual(payload_bytes(result), result["bytes_used"])
        self.assertLessEqual(result["bytes_used"], result["byte_budget"])
        self.assertNotIn("complete", result)
        self.assertNotIn("task_success", result)

    def test_pack_reports_omissions_and_keeps_later_small_entries(self) -> None:
        payload = {"nodes": {
            "large": {"name": "Large", "statement": "整项保留" * 2000},
            "small": {"name": "Small", "statement": "Small evidence."},
        }}
        library = CompiledLibrary.from_payload(payload)
        result = library.pack(["large", "small", "absent"], 1500)
        self.assertEqual(["small"], [entry["reference"] for entry in result["entries"]])
        self.assertEqual(library.get("small")["statement"], result["entries"][0]["statement"])
        self.assertIn({"reference": "large", "reason": "byte-budget"}, result["gaps"])
        self.assertIn({"reference": "absent", "reason": "unknown-reference"}, result["gaps"])
        self.assertEqual(payload_bytes(result), result["bytes_used"])
        self.assertLessEqual(result["bytes_used"], 1500)

    def test_pack_shares_claims_and_edges_without_losing_qualifiers(self) -> None:
        payload = library_payload()
        payload["claims"][0]["note"] = "Only for the stated domain. " * 80
        payload["claims"].append(copy.deepcopy(payload["claims"][0]))
        payload["claims"].append({**copy.deepcopy(payload["claims"][0]), "note": "Only for a restricted finite domain."})
        payload["edges"].append(copy.deepcopy(payload["edges"][0]))
        payload["edges"].append({**copy.deepcopy(payload["edges"][0]), "evidence": "The distinction applies only to the linear domain."})
        library = CompiledLibrary.from_payload(payload)
        packed = library.pack(["map-continuous", "map-finite-image", "norm"], 20000)
        self.assertEqual(2, len(packed["claims"]))
        self.assertEqual(2, len(packed["edges"]))
        self.assertEqual(library.get("map-continuous")["claims"][0], packed["claims"][0])
        self.assertEqual(library.get("map-continuous")["claims"][2], packed["claims"][1])
        self.assertEqual(["map-continuous", "map-finite-image"], [position["reference"] for position in packed["claims"][0]["positions"]])
        self.assertEqual(["map-continuous", "map-finite-image"], [position["reference"] for position in packed["claims"][0]["timeline"]])
        for edge in packed["edges"]:
            self.assertEqual("map-continuous", edge["source"]["reference"])
            self.assertEqual("map-finite-image", edge["target"]["reference"])
        self.assertEqual({payload["edges"][0]["evidence"], payload["edges"][2]["evidence"]}, {edge["evidence"] for edge in packed["edges"]})
        self.assertEqual([], packed["gaps"])

    def test_shared_packet_fits_more_whole_definitions_than_duplicated_get_records(self) -> None:
        refs = ["first", "second", "third"]
        payload = {"nodes": {
            ref: {
                "name": ref,
                "statement": "A complete definition with explicit quantifiers. " * 10,
                "conditions": ["The domain is finite.", "The codomain is normed."],
                "evidence": [{"section": "Definition", "quote": "The result is restricted to these conditions."}],
                "surfaces": {"query_forms": ["Retrieval expansion " * 50]},
            } for ref in refs
        }, "claims": [{
            "question": "Compare the exact scope of these definitions.",
            "note": "The comparison holds only under these assumptions. " * 45,
            "positions": [{"id": ref, "position": "Applies only to finite domains."} for ref in refs],
            "timeline": refs,
        }], "edges": [{"source": "first", "target": "second", "type": "compares-with", "evidence": "The comparison retains the domain restriction."}]}
        library = CompiledLibrary.from_payload(payload)
        budget = 7000
        duplicated = {"entries": [library.get(ref) for ref in refs], "gaps": [], "byte_budget": budget, "bytes_used": 0}
        self.assertGreater(payload_bytes(duplicated), budget)
        packet = library.pack(refs, budget)
        self.assertEqual(refs, [entry["reference"] for entry in packet["entries"]])
        for entry in packet["entries"]:
            original = payload["nodes"][entry["reference"]]
            self.assertEqual(original["statement"], entry["statement"])
            self.assertEqual(original["conditions"], entry["conditions"])
            self.assertEqual(original["evidence"], entry["evidence"])
        self.assertEqual(payload["claims"][0]["note"], packet["claims"][0]["note"])
        self.assertEqual(1, len(packet["claims"]))
        self.assertEqual(1, len(packet["edges"]))
        self.assertEqual([], packet["gaps"])
        self.assertEqual(payload_bytes(packet), packet["bytes_used"])
        self.assertLessEqual(packet["bytes_used"], budget)

    def test_packet_preserves_self_loop_and_reports_unresolved_neighbor_without_copying_it(self) -> None:
        payload = {"nodes": {"known": {"name": "Known", "statement": "A complete definition."}}, "edges": [
            {"source": "known", "target": "known", "type": "relates-to", "evidence": "Authored self relation."},
            {"source": "known", "target": "unresolved", "type": "requires", "evidence": "The external prerequisite is not resolved."},
        ]}
        library = CompiledLibrary.from_payload(payload)
        packet = library.pack(["known"], 5000)
        self.assertEqual(1, len(packet["edges"]))
        self.assertEqual(packet["edges"][0]["source"], packet["edges"][0]["target"])
        self.assertEqual(3, len(library.get("known")["edges"]))
        self.assertFalse(library.get("known")["edges"][2]["available"])
        self.assertEqual("unresolved", library.get("known")["edges"][2]["reference"])
        self.assertIn({"from": "known", "reference": "unresolved", "reason": "unresolved-reference"}, packet["gaps"])

    def test_joint_packet_keeps_full_claim_group_and_only_internal_edges(self) -> None:
        payload = library_payload()
        payload["nodes"]["third"] = {"name": "Third sense", "statement": "A third scoped definition."}
        payload["claims"][0]["positions"].append({"id": "third", "year": 2002, "position": "Applies only to nonempty finite domains."})
        payload["claims"][0]["timeline"].append("third")
        payload["edges"].append({"source": "map-continuous", "target": "norm", "type": "uses", "evidence": "The dependency is explicit."})
        library = CompiledLibrary.from_payload(payload)
        single = library.pack(["map-continuous"], 20000)
        self.assertEqual([], single["claims"])
        self.assertEqual([], single["edges"])
        joint = library.pack(["map-continuous", "map-finite-image"], 20000)
        self.assertEqual(library.get("map-continuous")["claims"], joint["claims"])
        self.assertEqual("third", joint["claims"][0]["positions"][2]["reference"])
        self.assertEqual("Applies only to nonempty finite domains.", joint["claims"][0]["positions"][2]["position"])
        self.assertEqual([2000, 2001, 2002], [position["year"] for position in joint["claims"][0]["positions"]])
        self.assertEqual(["map-continuous", "map-finite-image", "third"], [item["reference"] for item in joint["claims"][0]["timeline"]])
        self.assertEqual(1, len(joint["edges"]))
        self.assertEqual("map-finite-image", joint["edges"][0]["target"]["reference"])
        self.assertEqual(2, len(library.get("map-continuous")["edges"]))

    def test_relation_metadata_is_compact_but_authored_dependency_content_survives(self) -> None:
        payload = library_payload()
        payload["nodes"]["norm"].update(paper="source-image", year=2001)
        library = CompiledLibrary.from_payload(payload)
        original = library.get("map-continuous")
        packet = library.pack(["map-continuous", "norm"], 20000)
        entry = packet["entries"][0]
        self.assertEqual(original["source"], entry["source"])
        for key in ("depends_on", "relations"):
            self.assertIn("source", original[key][0])
            self.assertNotIn("source", entry[key][0])
            self.assertNotIn("year", entry[key][0])
            for field, value in original[key][0].items():
                if field not in {"source", "year"}:
                    self.assertEqual(value, entry[key][0][field])

    def test_large_single_neighbor_claim_remains_navigation_and_does_not_spend_packet_budget(self) -> None:
        payload = {"nodes": {
            "selected": {"name": "Selected", "statement": "All required science is here.", "conditions": ["Only on a finite domain."]},
            "neighbor": {"name": "Neighbor", "statement": "A neighboring definition."},
        }, "claims": [{
            "question": "A broad survey of a neighborhood.",
            "note": "Unrelated navigation context. " * 500,
            "positions": [{"id": "selected", "position": "Selected scope"}, {"id": "neighbor", "position": "Neighbor scope"}],
            "timeline": ["selected", "neighbor"],
        }], "edges": [{"source": "selected", "target": "neighbor", "type": "related-to", "evidence": "A navigational association."}]}
        library = CompiledLibrary.from_payload(payload)
        self.assertEqual(payload["claims"][0]["note"], library.get("selected")["claims"][0]["note"])
        packet = library.pack(["selected"], 1500)
        self.assertEqual(["selected"], [entry["reference"] for entry in packet["entries"]])
        self.assertEqual(payload["nodes"]["selected"]["conditions"], packet["entries"][0]["conditions"])
        self.assertEqual([], packet["claims"])
        self.assertEqual([], packet["edges"])
        self.assertEqual([], packet["gaps"])

    def test_claim_requires_distinct_packed_positions_not_repeated_or_omitted_positions(self) -> None:
        payload = {"nodes": {
            "first": {"name": "First", "statement": "The whole definition."},
            "oversize": {"name": "Oversize", "statement": "A long complete definition. " * 1000},
        }, "claims": [
            {"question": "A repeated unary position", "positions": [{"id": "first", "position": "One reading"}, {"id": "first", "position": "Another reading"}]},
            {"question": "A joint comparison", "positions": [{"id": "first", "position": "First scope"}, {"id": "oversize", "position": "Second scope"}]},
        ]}
        library = CompiledLibrary.from_payload(payload)
        packet = library.pack(["first", "oversize"], 1500)
        self.assertEqual(["first"], [entry["reference"] for entry in packet["entries"]])
        self.assertEqual([], packet["claims"])
        self.assertIn({"reference": "oversize", "reason": "byte-budget"}, packet["gaps"])
        self.assertEqual({"A repeated unary position", "A joint comparison"}, {claim["question"] for claim in library.get("first")["claims"]})

    def test_pack_reports_dependency_missing_from_selection(self) -> None:
        result = self.library.pack(["map-continuous", "map-continuous"], 20000)
        self.assertEqual(1, len(result["entries"]))
        self.assertIn({"from": "map-continuous", "reference": "norm", "name": "Norm", "reason": "dependency-not-packed"}, result["gaps"])

    def test_pack_rejects_budget_that_cannot_report_gaps(self) -> None:
        with self.assertRaisesRegex(CompiledRetrievalError, "cannot describe"):
            self.library.pack(["map-continuous"], 10)

    def test_from_path_uses_supplied_path_and_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "词条.json"
            path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
            library = CompiledLibrary.from_path(path)
            self.assertEqual(self.library.get("map-continuous"), library.get("map-continuous"))
            path.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(CompiledRetrievalError, "could not read"):
                CompiledLibrary.from_path(path)

    def test_generic_tokenization_handles_non_ascii_words_and_cjk(self) -> None:
        library = CompiledLibrary.from_payload({"nodes": {
            "unicode": {"name": "Naïve café Ω", "statement": "此处涉及先验条件。"},
            "other": {"name": "Unrelated", "statement": "Different evidence"},
        }})
        for query in ("NAÏVE", "café", "Ω", "先验"):
            self.assertEqual("unicode", library.search(query, 1)[0]["reference"])

    def test_empty_search_and_input_errors_are_explicit(self) -> None:
        self.assertEqual([], self.library.search(""))
        self.assertEqual([], self.library.search("unknownvocabulary"))
        self.assertEqual([], self.library.search("norm", 0))
        for invalid in ({}, {"nodes": []}, {"nodes": {"x": {"name": "X", "conditions": "wrong"}}}, {"nodes": {"x": {"id": "y", "name": "X"}}}):
            with self.subTest(payload=invalid), self.assertRaises(CompiledRetrievalError):
                CompiledLibrary.from_payload(invalid)
        with self.assertRaises(CompiledRetrievalError):
            self.library.search("norm", -1)
        with self.assertRaises(CompiledRetrievalError):
            self.library.pack("norm", 20000)


if __name__ == "__main__":
    unittest.main()
