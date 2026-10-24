# SPDX-License-Identifier: Apache-2.0
"""Public evidence cannot manufacture headroom with negative or noninteger debt."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'mcu_queue_report_verifier', ROOT / 'tools/verify_benchmark_report.py')
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


@pytest.mark.parametrize('mutation', ['negative_remaining', 'fractional_paid', 'boolean_remaining'])
def test_conserved_totals_do_not_authorize_invalid_costs(mutation):
    record = json.loads((ROOT / 'benchmarks/2026-10-24-mcu-queue-lora/results.json').read_bytes())
    REPORT.verify_mcu_queue(record)
    budget = record['budget']
    if mutation == 'negative_remaining':
        # Keep paid + remaining exactly at the cap: arithmetic alone used to pass.
        extra = 20_000_000
        record['runs']['baseline']['endpoints']['unit-b']['attempt_journal']['reserved_estimated_airtime_us'] += extra
        budget['inclusive_paid']['unit-b']['estimated_airtime_us'] += extra
        budget['remaining']['unit-b']['estimated_airtime_us'] -= extra
    elif mutation == 'fractional_paid':
        budget['failed_predecessor_paid']['unit-b']['attempts'] = 45.0
    else:
        budget['remaining']['unit-a']['attempts'] = True
    with pytest.raises(ValueError, match='invalid or over-budget queue cost'):
        REPORT.verify_mcu_queue(record)
