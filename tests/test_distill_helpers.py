import importlib.util
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/distill-paper/scripts'

def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

html = load('read_html')
lookup = load('lookup')

class DistillHelpersTests(unittest.TestCase):
    def test_source_extraction_preserves_formulas_anchors_and_table_text(self):
        source = '''<html><head><title>Test paper</title></head><body>
        <nav>UNRELATED NAV</nav><article><h1>Test paper</h1><section id="S2">
        <h2>2 Method</h2><p>Required conditions ''' + ('source text. '*100) + '''</p>
        <table id="E1" class="ltx_equation"><tr><td><math alttext="R_m^T R_n = R_{n-m}"><mi>DUPLICATE MATH</mi></math></td><td>(1)</td></tr></table>
        <p><math><semantics><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation></semantics></math></p>
        <table><tr><th>Condition</th><th>Value</th></tr><tr><td>Even dimension</td><td>d=2k</td></tr></table>
        <figure id="F1"><object data="figure.svg"></object><figcaption>Actual caption.</figcaption></figure>
        <a href="#E1">Equation 1</a><script>EVIL SCRIPT</script></section></article></body></html>'''
        text, info = html.extract(source, 'https://example.test/paper')
        for wanted in ['[S2]', '[E1]', 'R_m^T R_n = R_{n-m}', '$x^2$', 'Even dimension | d=2k', 'Actual caption.', '[Equation 1](#E1)']:
            self.assertIn(wanted, text)
        self.assertNotIn('DUPLICATE MATH', text)
        self.assertNotIn('UNRELATED NAV', text)
        self.assertNotIn('EVIL SCRIPT', text)
        self.assertTrue(info['warnings'])

    def test_nonpaper_and_missing_math_alternative_are_not_silently_accepted(self):
        with self.assertRaises(ValueError):html.extract('<title>Access denied</title><p>'+('x'*1200)+'</p>', 'https://example.test')
        with self.assertRaises(ValueError):html.extract('<title>Abstract</title><p>Short abstract.</p>', 'https://example.test')
        text, info = html.extract('<article>'+('text '*300)+'<math><mi>x</mi></math></article>', 'https://example.test')
        self.assertIn('[MathML: x]', text)
        self.assertTrue(any('MathML' in x for x in info['warnings']))

    def test_citation_targets_and_architecture_sources_survive_extraction(self):
        source = '<article><h1>Paper</h1><p>' + ('Reading text. '*100) + '''</p>
        <section id="method"><h2>Method</h2>
          <p>We reuse the block from <a href="#bib.original">Original (2020)</a>.</p>
          <figure id="architecture"><object id="diagram" data="figures/model.svg"></object>
            <figcaption>Module A feeds module B.</figcaption></figure>
        </section>
        <section id="appendix"><h2>Appendix</h2><p>Proof assumes a finite-dimensional space.</p></section>
        <section id="references"><ol>
          <li id="bib.original">Original architecture. <a href="https://doi.org/10.example/original">DOI</a></li>
        </ol></section></article>'''
        text, info = html.extract(source, 'https://example.test/papers/v1/index.html')
        self.assertIn('[Original (2020)](#bib.original)', text)
        self.assertIn('[bib.original]', text)
        self.assertIn('Original architecture.', text)
        self.assertIn('[DOI](https://doi.org/10.example/original)', text)
        self.assertIn('Proof assumes a finite-dimensional space.', text)
        self.assertIn('Module A feeds module B.', text)
        self.assertIn('bib.original', info['anchors'])
        self.assertEqual('https://example.test/papers/v1/figures/model.svg',
                         info['visual_sources'][0]['url'])

    def test_search_returns_deduplicated_previews_without_get(self):
        calls = []
        node = {'id':'one', 'type':'knowledge', 'label':'Known',
                'text':'Full text must not leak', 'entry':{'summary':'Short definition.', 'context':'Full conditions'}}
        def run(args):
            cmd=args[args.index('agent')+1:]; calls.append(cmd)
            if cmd[0]=='status':return {'snapshot_sha256':'same'}
            if cmd[0]=='resolve':return [
                {'query':'known','status':'exact','matches':[node]},
                {'query':'related','status':'missing','matches':[]}]
            if cmd[0]=='search':return {'result':{'results':[{'node_id':'one','label':'Known'}]}}
            raise AssertionError(cmd)
        result=lookup.lookup(['--vault','test'], ['known','related'],run)
        self.assertEqual([],result['entries'])
        self.assertEqual(1,len(result['candidates']))
        self.assertEqual('Short definition.',result['candidates'][0]['summary'])
        self.assertNotIn('Full text', str(result))
        self.assertNotIn('Full conditions', str(result))
        self.assertFalse(any(cmd[0]=='get' for cmd in calls))
        self.assertEqual('missing',result['results'][1]['identity_status'])

    def test_agent_can_select_late_candidate_after_more_than_twenty_noisy_hits(self):
        terms = [f'term{i}' for i in range(30)]
        reads = []
        def run(args):
            cmd=args[args.index('agent')+1:]
            if cmd[0]=='status':return {'snapshot_sha256':'same'}
            if cmd[0]=='resolve':return [
                {'query':t,'status':'missing','matches':[]} if t in terms else
                {'query':t,'status':'exact','matches':[{'id':t,'label':t,'text':'Candidate summary.'}]}
                for t in cmd[1:]]
            if cmd[0]=='search':return {'result':{'results':[
                *({'node_id':cmd[1]+f'-noise-{i}','label':'Noise'} for i in range(3)),
                *([{'node_id':'unitary-matrix','label':'Unitary matrix'}] if cmd[1]==terms[-1] else [])]}}
            if cmd[0]=='get':
                reads.append(cmd[1])
                return {'node':{'label':'Unitary matrix','text':'Q* Q = I.',
                                'entry':{'context':'Complex square matrix.'},
                                'provenance':{'web':'https://example.test/note'}}}
            raise AssertionError(cmd)
        searched=lookup.lookup(['--vault','test'],terms,run)
        self.assertEqual(91,len(searched['candidates']))
        self.assertEqual('unitary-matrix',searched['candidates'][-1]['id'])
        self.assertEqual([],reads)
        selected=lookup.lookup(['--vault','test'],['unitary-matrix','unitary-matrix'],run,read=True)
        self.assertEqual(['unitary-matrix'],reads)
        self.assertEqual('Complex square matrix.',selected['entries'][0]['details']['context'])
        self.assertEqual('https://example.test/note',selected['entries'][0]['provenance']['web'])

    def test_explicit_read_has_no_twenty_entry_cutoff_and_reports_truncation(self):
        def run(args):
            cmd=args[args.index('agent')+1:]
            if cmd[0]=='status':return {'snapshot_sha256':'same'}
            if cmd[0]=='get':return {'node':{'label':cmd[1],'text':'x'*5001}}
            raise AssertionError(cmd)
        result=lookup.lookup(['--vault','test'],[f'node-{i}' for i in range(30)],run,read=True)
        self.assertEqual(30,len(result['entries']))
        self.assertTrue(all(e['text_truncated'] for e in result['entries']))

    def test_lookup_errors_and_changed_generation_do_not_become_missing(self):
        def broken(args):raise OSError('CLI unavailable')
        result=lookup.lookup(['--vault','test'], ['one'],broken)
        self.assertTrue(result['errors']);self.assertEqual([],result['results'])
        states=iter(['before','after'])
        def run(args):
            if args[-1]=='status':return {'snapshot_sha256':next(states)}
            if 'resolve' in args:return [{'query':'one','status':'missing','matches':[]}]
            if 'search' in args:return {'error':{'code':'lookup-failed'}}
            raise AssertionError(args)
        result=lookup.lookup(['--vault','test'], ['one'],run)
        self.assertIn('error',result['results'][0]);self.assertFalse(result['generation_unchanged']);self.assertTrue(result['errors'])

    def test_preview_failure_retains_candidate_handle_and_does_not_fetch_body(self):
        def run(args):
            cmd=args[args.index('agent')+1:]
            if cmd[0]=='status':return {'snapshot_sha256':'same'}
            if cmd==['resolve','term']:return [{'query':'term','status':'missing','matches':[]}]
            if cmd[0]=='resolve':raise TimeoutError('preview timed out')
            if cmd[0]=='search':return {'result':{'results':[{'node_id':'candidate','label':'Candidate'}]}}
            raise AssertionError(cmd)
        result=lookup.lookup(['--vault','test'],['term'],run)
        self.assertTrue(result['errors'])
        self.assertEqual('candidate',result['candidates'][0]['id'])
        self.assertFalse(result['candidates'][0]['preview_available'])

    def test_acronym_substrings_are_not_returned_as_knowledge(self):
        def run(args):
            cmd=args[args.index('agent')+1:]
            if cmd[0]=='status':return {'snapshot_sha256':'same'}
            if cmd==['resolve','RoPE']:return [{'query':'RoPE','status':'missing','matches':[]}]
            if cmd[0]=='resolve':return [{'query':'relevant','status':'exact','matches':[{'id':'relevant','label':'RoPE mechanism','text':'Definition'}]}]
            if cmd[0]=='search':return {'result':{'results':[{'node_id':'irrelevant','label':'properties of measures'},{'node_id':'relevant','label':'RoPE mechanism'}]}}
            raise AssertionError(cmd)
        result=lookup.lookup(['--vault','test'],['RoPE'],run)
        self.assertEqual(['relevant'],[c['id'] for c in result['candidates']])
