"""离线比较冻结人工标签与规则结果；不连接服务，不读取媒体正文。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

GROUPS = ('media', 'subtitle', 'poster', 'nfo', 'extras', 'readme', 'disc', 'suspected_ad', 'other')
MAX_BYTES = 2 * 1024 * 1024
MAX_SAMPLES = 10000


class ValidationError(ValueError):
    pass


def require(condition):
    if not condition:
        raise ValidationError('invalid_or_incomparable_input')


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value) is not None


def digest(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def uniqueObject(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def readDocument(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES)
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=uniqueObject)
    require(isinstance(document, dict))
    return document, sha256(raw).hexdigest()


def validate(document, *, predictions):
    common = {'formatVersion', 'sampleSetId', 'sourceSnapshotSha256', 'samples'}
    extra = {'ruleVersion', 'ruleSha256', 'observationCurrent'} if predictions else {'labelVersion'}
    require(set(document) == common | extra)
    require(type(document['formatVersion']) is int and document['formatVersion'] == 1)
    require(identifier(document['sampleSetId']) and digest(document['sourceSnapshotSha256']))
    if predictions:
        require(identifier(document['ruleVersion']) and digest(document['ruleSha256']))
        require(document['observationCurrent'] is True)
    else:
        require(identifier(document['labelVersion']))
    rows = document['samples']
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_SAMPLES)
    indexed = {}
    for row in rows:
        require(isinstance(row, dict))
        fields = {'sampleId', 'prediction', 'protected'} if predictions else {'sampleId', 'group', 'label'}
        require(set(row) == fields and identifier(row['sampleId']))
        require(row['sampleId'] not in indexed)
        if predictions:
            require(row['prediction'] in ('keep', 'review') and type(row['protected']) is bool)
        else:
            require(row['group'] in GROUPS and row['label'] in ('keep', 'ad', 'uncertain'))
        indexed[row['sampleId']] = row
    return indexed


def summarize(rows):
    matrix = dict(TP=0, FP=0, TN=0, FN=0)
    uncertain = 0
    for label, prediction in rows:
        if label == 'uncertain':
            uncertain += 1
        else:
            key = {('ad', 'review'): 'TP', ('ad', 'keep'): 'FN',
                   ('keep', 'review'): 'FP', ('keep', 'keep'): 'TN'}[(label, prediction)]
            matrix[key] += 1
    def rate(numerator, denominator):
        return {'numerator': numerator, 'denominator': denominator,
                'value': numerator / denominator if denominator else None}
    return {'total': len(rows), 'determined': len(rows) - uncertain, 'uncertain': uncertain,
            'matrix': matrix,
            'precision': rate(matrix['TP'], matrix['TP'] + matrix['FP']),
            'recall': rate(matrix['TP'], matrix['TP'] + matrix['FN']),
            'falsePositiveRate': rate(matrix['FP'], matrix['FP'] + matrix['TN'])}


def evaluate(labels, predictions):
    humans = validate(labels, predictions=False)
    rules = validate(predictions, predictions=True)
    require(labels['sampleSetId'] == predictions['sampleSetId'])
    require(labels['sourceSnapshotSha256'] == predictions['sourceSnapshotSha256'])
    require(humans.keys() == rules.keys())
    groups = {group: [] for group in GROUPS}
    violations = []
    for sampleId, row in humans.items():
        predicted = rules[sampleId]
        groups[row['group']].append((row['label'], predicted['prediction']))
        if predicted['protected'] and predicted['prediction'] == 'review':
            violations.append(sampleId)
    return {'formatVersion': 1, 'sampleSetId': labels['sampleSetId'],
            'sourceSnapshotSha256': labels['sourceSnapshotSha256'], 'labelVersion': labels['labelVersion'],
            'ruleVersion': predictions['ruleVersion'], 'ruleSha256': predictions['ruleSha256'],
            'overall': summarize([pair for rows in groups.values() for pair in rows]),
            'groups': {group: summarize(rows) for group, rows in groups.items() if rows},
            'protectedReviewSampleIds': sorted(violations),
            'status': 'requires_protection_review' if violations else 'statistics_only',
            'remoteFreshnessVerified': False, 'humanLabelIndependenceVerified': False,
            'authorizesFileActions': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--labels-sha256', required=True)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(digest(args.labels_sha256))
        labels, labelHash = readDocument(args.labels)
        require(labelHash == args.labels_sha256)
        predictions, predictionHash = readDocument(args.predictions)
        report = evaluate(labels, predictions)
        report['inputs'] = {'labelsSha256': labelHash, 'predictionsSha256': predictionHash}
        # 不覆盖输入、旧标签或既有报告；报告只含白名单匿名字段。
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=True, indent=2)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'samples': report['overall']['total']}))
        return 2 if report['protectedReviewSampleIds'] else 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('ad_label_validation_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
