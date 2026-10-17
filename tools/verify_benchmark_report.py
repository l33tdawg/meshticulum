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



def verify_sdk_files(record):
    require(record['schema']=='meshticulum.published-sdk-file-samples.v1','unsupported SDK report')
    require(re.fullmatch('[0-9a-f]{40}',record['core_commit']) is not None,'invalid source pin')
    require(record['modulation']=='GFSK' and record['modem_bit_rate_bps']==250000,'wrong modulation scope')
    require(record['configured_profile']=={
        'class':'Gfsk250Profile','frequency_hz':921375000,'chip_power_dbm':0,
        'bit_rate_bps':250000,'deviation_hz':62500,'rx_bandwidth_hz':467000,
        'preamble_bits':32},'tested profile mismatch')
    require(record['scope_to_closure_includes']==['grant delivery','endpoint startup','coordination','verification','endpoint cleanup']
            and record['scope_to_closure_excludes']==['offline file generation','signing','build','staging'],
            'scope-to-closure timing scope mismatch')
    for field in ('measured_range_metres','measured_RF_airtime_us','measured_energy_joules','measured_eirp_dbm'):
        require(record[field] is None,'missing measurement invented: '+field)
    for field in ('native_private_qualified','sustained_goodput_qualified','alpha_accepted'):
        require(record[field] is False,'finite samples relabeled as qualification: '+field)
    seen_ids=set();seen_files=set()
    boundaries=('first_sender_startup_to_final_authenticated_receipt',
                'first_DATA_worker_call_to_final_authenticated_receipt',
                'scope_issue_to_both_endpoints_closed')
    require(bool(record['samples']),'empty SDK samples')
    for row in record['samples']:
        require(row['sample_id'] not in seen_ids,'duplicate sample')
        seen_ids.add(row['sample_id'])
        require(row['useful_sha256'] not in seen_files,'fresh input reused')
        seen_files.add(row['useful_sha256'])
        require(row['fresh_files_measured']==1 and row['preexisting_cached_parts']==0,'fresh-file denominator mismatch')
        parts=row['part_useful_bytes'];size=row['useful_decoded_bytes']
        require(type(size) is int and size>0 and parts and all(type(x) is int and 0<x<=23900 for x in parts),'invalid file/part size')
        require(sum(parts)==size,'file/part length mismatch')
        require(row['publisher_and_whole_file_independently_verified'] is True and row['within_original_file_window'] is True,'missing verified completion')
        children=row['endpoint_children']
        require(children['total']==children['exit_zero']==2*len(parts) and children['forced_cleanup']==0,'endpoint completion mismatch')
        require(row['services_and_boot_unchanged'] is True,'service/boot state changed')
        for field in ('data_retransmissions','new_receiver_drops'):
            require(type(row[field]) is int and row[field]>=0,'invalid '+field)
        require(row['default_receiver_grace_us']==20000000,'changed fallback grace')
        mode=row['receiver_retirement_mode']
        require(mode in ('default-20-second-receiver-grace','authenticated-sender-receipt-confirmation'),'unknown retirement mode')
        require(row['receiver_confirmations_accepted']==(len(parts) if mode=='authenticated-sender-receipt-confirmation' else 0),'confirmation count mismatch')
        require(set(row['boundaries'])==set(boundaries),'timing boundary mismatch')
        for interval in row['boundaries'].values():
            elapsed=interval['elapsed_us']
            require(type(elapsed) is int and elapsed>0,'invalid elapsed interval')
            require(close(interval['useful_decoded_Bps'],size*1000000/elapsed),'SDK goodput arithmetic mismatch')
        intervals=row['boundaries']
        require(intervals[boundaries[1]]['elapsed_us']<=intervals[boundaries[0]]['elapsed_us']<=intervals[boundaries[2]]['elapsed_us'],'inverted timing boundaries')
        for field in ('useful_sha256','source_result_sha256','verified_summary_sha256'):
            require(re.fullmatch('[0-9a-f]{64}',row[field]) is not None,'invalid '+field)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',nargs='?',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=[args.report] if args.report else [root/'benchmarks/2026-10-09-radio/radio-results.json',
                                           root/'benchmarks/2026-10-17-public-sdk/results.json']
    for path in paths:
        record=json.loads(path.read_text())
        if record['schema']=='meshticulum.published-sdk-file-samples.v1':
            verify_sdk_files(record)
            print('Verified '+str(len(record['samples']))+' distinct fresh SDK files, part/confirmation/endpoint counts and all whole-file timing arithmetic. Qualification remains open.')
        else:
            verify(record)
            print('Verified 30 unique RF samples, separate directional distributions and 1 retained setup failure. Missing metrics remain null.')


if __name__=='__main__':main()
