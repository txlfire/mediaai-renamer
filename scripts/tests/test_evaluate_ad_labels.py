"""合成统计与CLI边界验证，不导入应用或访问运行库。"""

import copy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'evaluate-ad-labels.py'
spec = importlib.util.spec_from_file_location('adLabels', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdLabelsTest(unittest.TestCase):
    def documents(self):
        common = {'formatVersion': 1, 'sampleSetId': 'synthetic_01', 'sourceSnapshotSha256': 'a'*64}
        labels = {**common, 'labelVersion': 'v1', 'samples': [
            {'sampleId': f's{i}', 'group': 'media', 'label': label}
            for i, label in enumerate(('ad', 'ad', 'keep', 'keep', 'uncertain'))]}
        predictions = {**common, 'ruleVersion': 'metadata-advice-v1', 'ruleSha256': 'b'*64,
                       'observationCurrent': True, 'samples': [
            {'sampleId': f's{i}', 'prediction': prediction, 'protected': False}
            for i, prediction in enumerate(('review', 'keep', 'review', 'keep', 'review'))]}
        return labels, predictions

    def testMatrixAndUncertain(self):
        report = module.evaluate(*self.documents())
        overall = report['overall']
        self.assertEqual(overall['matrix'], dict(TP=1, FP=1, TN=1, FN=1))
        self.assertEqual(overall['uncertain'], 1)
        for name in ('precision', 'recall', 'falsePositiveRate'):
            self.assertEqual(overall[name], dict(numerator=1, denominator=2, value=0.5))

    def testZeroDenominatorsAreNull(self):
        overall = module.summarize([('uncertain', 'keep')])
        self.assertIsNone(overall['precision']['value'])
        self.assertIsNone(overall['recall']['value'])
        self.assertIsNone(overall['falsePositiveRate']['value'])

    def testEveryMatrixCellIndependently(self):
        for pair, key in ((('ad', 'review'), 'TP'), (('ad', 'keep'), 'FN'),
                          (('keep', 'review'), 'FP'), (('keep', 'keep'), 'TN')):
            with self.subTest(pair=pair):
                matrix = module.summarize([pair])['matrix']
                self.assertEqual(matrix[key], 1)
                self.assertEqual(sum(matrix.values()), 1)

    def testGroupsAndProtectedReview(self):
        labels, predictions = self.documents()
        labels['samples'][0]['group'] = 'subtitle'
        predictions['samples'][0]['protected'] = True
        report = module.evaluate(labels, predictions)
        self.assertEqual(report['groups']['subtitle']['total'], 1)
        self.assertEqual(report['protectedReviewSampleIds'], ['s0'])
        self.assertEqual(report['status'], 'requires_protection_review')
        self.assertFalse(report['authorizesFileActions'])

    def testIdentityAndFreshnessMismatchRejected(self):
        for key, value in (('sampleSetId', 'other'), ('sourceSnapshotSha256', 'c'*64),
                           ('observationCurrent', False), ('formatVersion', True)):
            labels, predictions = self.documents()
            predictions[key] = value
            with self.subTest(key=key), self.assertRaises(module.ValidationError):
                module.evaluate(labels, predictions)

    def testDuplicateMissingAndExtraSamplesRejected(self):
        for action in ('duplicate', 'missing', 'extra'):
            labels, predictions = self.documents()
            if action == 'duplicate':
                predictions['samples'].append(copy.deepcopy(predictions['samples'][0]))
            elif action == 'missing':
                predictions['samples'].pop()
            else:
                predictions['samples'].append({'sampleId': 'extra', 'prediction': 'keep', 'protected': False})
            with self.subTest(action=action), self.assertRaises(module.ValidationError):
                module.evaluate(labels, predictions)

    def testUnknownFieldsPathsAndWrongTypesRejected(self):
        for field, value in (('path', 'private-name'), ('sampleId', '../secret'), ('protected', 1), ('prediction', 'ad')):
            labels, predictions = self.documents()
            predictions['samples'][0][field] = value
            with self.subTest(field=field), self.assertRaises(module.ValidationError):
                module.evaluate(labels, predictions)

    def testSampleLimitAndEmptyRejected(self):
        labels, predictions = self.documents()
        for rows in ([], predictions['samples'] * 2001):
            predictions['samples'] = rows
            with self.assertRaises(module.ValidationError):
                module.evaluate(labels, predictions)

    def testDuplicateJsonKeysAndLargeInputRejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.json'
            for raw in (b'{"a":1,"a":2}', b' '*(module.MAX_BYTES + 1)):
                path.write_bytes(raw)
                with self.assertRaises(module.ValidationError):
                    module.readDocument(path)

    def testCliFreezeHashNoOverwriteAndSafeErrors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            labels, predictions = self.documents()
            labelPath, predictionPath, output = (root / name for name in ('labels.json', 'predictions.json', 'report.json'))
            labelPath.write_text(json.dumps(labels), encoding='utf-8')
            predictionPath.write_text(json.dumps(predictions), encoding='utf-8')
            command = [sys.executable, str(SCRIPT), '--labels', str(labelPath), '--labels-sha256',
                       sha256(labelPath.read_bytes()).hexdigest(), '--predictions', str(predictionPath), '--output', str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)['overall']['matrix']['FP'], 1)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), saved)
            self.assertNotIn(str(root), result.stderr)
            labelPath.write_text('{}', encoding='utf-8')
            command[-1] = str(root / 'other.json')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((root / 'other.json').exists())


if __name__ == '__main__':
    unittest.main()
