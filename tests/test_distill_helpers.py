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

    def test_lookup_batches_identity_deduplicates_content_and_keeps_provenance(self):
        calls = []
        def run(args):
            args=args[args.index('agent')+1:];calls.append(args)
            if args[0]=='status':return {'snapshot_sha256':'same'}
            if args[0]=='resolve':return [{'query':'known','status':'exact','matches':[{'id':'one','type':'knowledge'}]}, {'query':'alias','status':'missing','matches':[]}]
            if args[0]=='search':return {'result':{'results':[{'node_id':'one'}]}}
            if args[0]=='get':return {'node':{'label':'Known','text':'Actual condition and definition.','entry':{'context':'Requires a real inner-product space.','prerequisites':['Finite dimension']},'provenance':{'web':'https://example.test/note#one'}}}
            raise AssertionError(args)
        result=lookup.lookup(['--vault','test'], ['known','alias'],run)
        self.assertEqual(1, sum(x[0]=='resolve' for x in calls))
        self.assertEqual(1, sum(x[0]=='get' for x in calls))
        self.assertEqual(1,len(result['entries']))
        self.assertEqual('Requires a real inner-product space.', result['entries'][0]['details']['context'])
        self.assertEqual(['Finite dimension'], result['entries'][0]['details']['prerequisites'])
        self.assertEqual('https://example.test/note#one',result['entries'][0]['provenance']['web'])
        self.assertEqual('missing',result['results'][1]['identity_status'])
        self.assertIn('not established',result['results'][1]['retrieval'])

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
