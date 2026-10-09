"""A hidden knowledge tree remains a single writable, portable authority."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from kgdistiller.capture import prepare_capture
from kgdistiller.cli import load_state, synchronize, make_artifacts
from kgdistiller.entry_markdown import load_entry_authorities
from kgdistiller.ingest import apply_ingest, load_request
from kgdistiller.knowledge_paths import knowledge_root
from kgdistiller.query import GraphView, get
from kgdistiller.store import snapshot_store, verify_store
from kgdistiller.vault_registry import ensure_vault_manifest, load_vault_manifest, _nearest_project_root
from tests import test_capture


class HiddenKnowledgeRootTest(unittest.TestCase):
    def setUp(self):
        fixture = test_capture.CaptureTest('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root
        state = load_state(fixture.paths.graph_dir)
        vault = ensure_vault_manifest(self.root)
        (self.root / 'knowledge').rename(self.root / '.knowledge')
        self.paths = replace(fixture.paths, **{
            key: self.root / '.knowledge' / getattr(fixture.paths, key).relative_to(self.root / 'knowledge')
            for key in ('registry', 'graph_dir', 'identities', 'alignments', 'typst_registry')
        })
        # Fixtures have no accepted bodies yet; sync rebuilds path inventories.
        synchronize(self.root, self.paths.registry, self.paths.graph_dir, self.paths.typst_registry,
                    identities=self.paths.identities, alignments=self.paths.alignments,
                    files=[], course=None, subject=None, write=True)
        self.assertEqual(load_vault_manifest(self.root), vault)

    def test_capture_queries_and_cli_use_hidden_tree(self):
        result = prepare_capture(self.paths, self.fixture.payload(), self.root / '.knowledge/build/capture')
        apply_ingest(self.paths, load_request(Path(result['artifacts']['apply']), mode='apply'))
        state = load_state(self.paths.graph_dir)
        beta = get(GraphView.load(self.paths.graph_dir), 'beta')['node']
        self.assertEqual(beta['text'], self.fixture.payload()['text'])
        self.assertTrue(beta['properties']['entry_authority'].startswith('.knowledge/entries/'))
        self.assertTrue((self.root / '.knowledge/entries/beta.md').is_file())
        self.assertFalse((self.root / 'knowledge').exists())
        self.assertEqual(_nearest_project_root(self.root / 'notes'), self.root.resolve())
        result = subprocess.run([sys.executable, '-m', 'kgdistiller', '--repo-root', str(self.root), 'check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse((self.root / 'knowledge').exists())

    def test_hidden_store_snapshot_and_verification(self):
        prepared = prepare_capture(self.paths, self.fixture.payload(), self.root / '.knowledge/build/capture')
        apply_ingest(self.paths, load_request(Path(prepared['artifacts']['apply']), mode='apply'))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'snapshot'
            snapshot_store(self.root, output, registry=self.paths.registry,
                           graph_dir=self.paths.graph_dir, identities=self.paths.identities,
                           alignments=self.paths.alignments)
            self.assertEqual(verify_store(output)['status'], 'verified')
            self.assertTrue((output / '.knowledge/entries/beta.md').is_file())
            self.assertFalse((output / 'knowledge').exists())

    def test_ambiguous_roots_are_rejected(self):
        (self.root / 'knowledge').mkdir()
        with self.assertRaisesRegex(ValueError, 'both'):
            knowledge_root(self.root)
