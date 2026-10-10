"""Check published benchmark arithmetic without hardware or private captures."""
import argparse
import json
import math
from pathlib import Path
import re
import statistics


def require(condition, reason):
    if not condition: raise ValueError(reason)


def distribution(values):
    values=sorted(values)
    return {'n':len(values),'min':values[0],'median':statistics.median(values),
            'p95_nearest_rank':values[math.ceil(.95*len(values))-1],'max':values[-1]}


def close(actual, expected):
    return isinstance(actual,(int,float)) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)


def verify(record):
    require(record['schema']=='meshticulum.published-radio-bench.v1','unsupported report')
    require(record['radios_per_device']==1 and record['payload_bytes']==8192,'foreign bench conditions')
    require(set(record['directions'])=={'ca','cb','ab'},'wrong directions')
    seen=set(); retransmits=0; over_target=0
    for name,group in record['directions'].items():
        rates=[]; durations=[]; group_retries=0
        require(group['recorded_runs']==group['completed_runs']==len(group['samples'])==10,'wrong sample denominator')
        for row in group['samples']:
            require(row['run_id'] not in seen,'duplicate sample');seen.add(row['run_id'])
            require(row['status']=='completed' and row['payload_bytes']==8192,'incomplete or mismatched sample')
            duration=row['sender_start_to_flow_ack_seconds']
            rate=row['sender_acknowledged_goodput_Bps']
            require(type(duration) in (int,float) and math.isfinite(duration) and duration>0,'invalid interval')
            require(close(rate,row['payload_bytes']/duration),'goodput arithmetic mismatch')
            require(row['matched_data_rf_packets']>=math.ceil(8192/193) and row['matched_ack_rf_packets']>0,'missing matched packets')
            require(row['sender_rf_done_attempts']>=row['matched_data_rf_packets'] and row['receiver_rf_done_attempts']>=row['matched_ack_rf_packets'],'invalid completion counts')
            require(type(row['sender_data_retransmissions']) is int and row['sender_data_retransmissions']>=0,'invalid retries')
            for digest in row['capture_sha256'].values(): require(re.fullmatch('[0-9a-f]{64}',digest) is not None,'invalid capture hash')
            rates.append(rate);durations.append(duration);group_retries+=row['sender_data_retransmissions'];over_target+=rate>=1000
        for field,values in [('goodput_Bps',rates),('sender_start_to_flow_ack_seconds',durations)]:
            expected=distribution(values);require(set(group[field])==set(expected),'wrong distribution fields')
            for key,value in expected.items():require(close(group[field][key],value),name+' '+field+' '+key+' mismatch')
        require(group_retries==group['sender_data_retransmissions_total'],'direction retry mismatch')
        retransmits+=group_retries
    require(len(seen)==record['rf_samples_completed']==30,'wrong RF count')
    require(retransmits==record['data_retransmissions_total']==4,'wrong total retries')
    require(over_target==record['samples_at_or_above_1000_Bps']==29,'wrong threshold count')
    require(record['managed_session_attempts']==31 and record['setup_failures']==record['setup_failure']['count']==1,'setup denominator mismatch')
    require(record['setup_failure']['rf_sample'] is False,'setup failure relabeled as RF')
    require(record['target_goodput_Bps']==1500 and record['target_met_in_repeated_samples'] is False,'target relabeled as measurement')
    require(all(row['sender_acknowledged_goodput_Bps']<1500 for group in record['directions'].values() for row in group['samples']),'unexpected target sample')
    for field in ('matched_meshcore_samples','matched_reticulum_samples','measured_airtime_seconds','measured_energy_joules','one_way_latency_seconds','measured_eirp_dbm'):
        require(record[field] is None,'missing measurement invented: '+field)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',nargs='?',type=Path,default=Path(__file__).resolve().parents[1]/'benchmarks/2026-10-09-radio/radio-results.json')
    args=parser.parse_args();verify(json.loads(args.report.read_text()))
    print('Verified 30 unique RF samples, separate directional distributions and 1 retained setup failure. Missing metrics remain null.')


if __name__=='__main__':main()
