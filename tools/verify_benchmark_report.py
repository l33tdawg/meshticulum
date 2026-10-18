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
        if 'attempt_ledger' in row:
            ledger=row['attempt_ledger']
            require(set(ledger)=={'unit-a','unit-c'},'wrong attempt ledger endpoints')
            for unit,attempts in ledger.items():
                require(type(attempts.get('total_attempts')) is int and attempts['total_attempts']>=0,
                        unit+' invalid attempt total')
                require(type(attempts.get('reserved_estimated_airtime_us')) is int
                        and attempts['reserved_estimated_airtime_us']>=0,
                        unit+' invalid reserved airtime')
                require(attempts.get('recovered_unknown')==0 and attempts.get('fenced') is False,
                        unit+' unsettled attempt ledger')
        if 'mcu_boot_counters' in row:
            counters=row['mcu_boot_counters']
            advance=counters['attempts_after']-counters['attempts_before']
            require(advance==row['attempt_ledger']['unit-a']['total_attempts'],
                    'MCU attempt advance does not match receiver journal')
            require(counters['reserved_estimated_airtime_us_after']
                    >=counters['reserved_estimated_airtime_us_before'],
                    'MCU reserved airtime moved backwards')
            require(counters['raw_receive_drops_after']-counters['raw_receive_drops_before']
                    ==row['new_receiver_drops'],'MCU receive-drop advance mismatch')
        if 'part_interval_sum_us' in row:
            require(row['part_interval_sum_us']+row['coordination_gap_us']
                    ==row['boundaries'][boundaries[0]]['elapsed_us'],
                    'part interval and coordination gap do not match sender boundary')
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


def verify_lora_comparison(record):
    require(record['schema']=='meshticulum.lora-sf7-sf8-comparison.v1','unsupported LoRa report')
    require(re.fullmatch('[0-9a-f]{40}',record['source_commit']) is not None,'invalid LoRa source pin')
    require(re.fullmatch('[0-9a-f]{64}',record['sdk_sha256']) is not None,'invalid LoRa SDK hash')
    require(record['repeats']==3 and len(record['payloads'])==3,'wrong LoRa repeat count')
    require(len(record['samples'])==6,'wrong LoRa sample count')
    payloads={row['repeat']:row for row in record['payloads']}
    require(set(payloads)=={0,1,2},'wrong LoRa payload repeats')
    require(len({row['sha256'] for row in payloads.values()})==3,'LoRa pair payload reused across repeats')
    require(all(row['bytes']==8164 and re.fullmatch('[0-9a-f]{64}',row['sha256']) for row in payloads.values()),'wrong LoRa payload')
    expected_profiles={
        'sf7-500':{'spreading_factor':7,'bandwidth_hz':500000,'coding_rate':5},
        'sf8-500':{'spreading_factor':8,'bandwidth_hz':500000,'coding_rate':5},
    }
    grouped={name:[] for name in expected_profiles}
    for row in record['samples']:
        name=row['profile']
        require(name in grouped and row['repeat'] in payloads,'foreign LoRa sample')
        require(row['payload_sha256']==payloads[row['repeat']]['sha256']
                ==row['radio_manifest_sha256'],'LoRa pair payload mismatch')
        require(row['receiver']['receiver_chunk_verified'] is True
                and row['sender']['sender_peer_receipt'] is True,'LoRa completion missing')
        for unit in ('receiver','sender'):
            journal=row[unit]['attempt_journal']
            require(journal['recovered_unknown']==0 and journal['fenced'] is False,'unsettled LoRa attempt')
            profile=row[unit]['profile']
            require(all(profile[key]==value for key,value in expected_profiles[name].items()),
                    'LoRa profile mismatch')
            require(profile['frequency_hz']==921375000 and profile['chip_power_dbm']==0
                    and profile['preamble']==8 and profile['sync_word']==0x42,
                    'LoRa fixed conditions mismatch')
        confirmation=row['receiver']['receipt_confirmation']
        require(confirmation['enabled'] is True and confirmation['peer_receipt_confirmed'] is True
                and row['sender']['receipt_confirmation']['outcome']=='RF_DONE',
                'LoRa receipt confirmation missing')
        grouped[name].append(row)
    require(all(len(rows)==3 for rows in grouped.values()),'wrong LoRa per-profile count')
    for name,rows in grouped.items():
        durations=[row['sender']['timing']['armed_to_complete_us']/1e6 for row in rows]
        median=statistics.median(durations)
        summary=record['summary'][name]
        require(summary['samples']==3 and summary['payload_bytes']==8164,'wrong LoRa summary denominator')
        require(math.isclose(summary['median_sender_armed_to_receipt_seconds'],median,
                             rel_tol=0,abs_tol=.000001),'LoRa timing median mismatch')
        require(math.isclose(summary['median_sender_armed_to_receipt_useful_Bps'],
                             8164/median,rel_tol=0,abs_tol=.001),'LoRa goodput median mismatch')
        require(summary['receiver_attempts']==[row['receiver']['attempt_journal']['attempts'] for row in rows],
                'LoRa receiver attempt list mismatch')
        require(summary['sender_attempts']==[row['sender']['attempt_journal']['attempts'] for row in rows],
                'LoRa sender attempt list mismatch')
    baseline=record['baseline']['unit-a'];after=record['mcu_after']
    bounds=record['baseline'].get('captured_between_epoch_us')
    require(type(bounds) is list and len(bounds)==2
            and all(type(value) is int and value>0 for value in bounds)
            and bounds[0]<bounds[1],'invalid LoRa baseline capture bounds')
    require(record['confirmation']['mcu_boot_changed'] is False,
            'LoRa final batch crossed MCU boots')
    receiver_attempts=sum(row['receiver']['attempt_journal']['attempts']
                          for row in record['samples'])
    require(after['boot_attempts']-baseline['boot_attempts']==receiver_attempts,
            'MCU attempt advance does not match LoRa receiver journals')
    require(after['boot_reserved_estimated_airtime_us']>=baseline['boot_reserved_estimated_airtime_us'],
            'MCU LoRa airtime regressed')
    require(after['dropped_receive']==baseline['dropped_receive']==0,
            'LoRa receiver drops changed')
    confirmation=record['confirmation']
    require(confirmation['receiver_chunks_verified']==6
            and confirmation['sender_peer_receipts']==6
            and confirmation['authenticated_receipt_confirmations']==6
            and confirmation['recovered_unknown_attempts']==0
            and confirmation['fenced_attempts']==0
            and confirmation['mcu_boot_changed'] is False
            and confirmation['mcu_attempt_delta']==receiver_attempts
            and confirmation['mcu_receive_drop_delta']==0,
            'LoRa summary integrity mismatch')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',nargs='?',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=[args.report] if args.report else [root/'benchmarks/2026-10-09-radio/radio-results.json',
                                           root/'benchmarks/2026-10-17-public-sdk/results.json',
                                           root/'benchmarks/2026-10-18-lora-sf7-sf8/results.json',
                                           root/'provenance/PUBLIC_INSTALLED_SDK_PROOF.json']
    for path in paths:
        record=json.loads(path.read_text())
        if record['schema']=='meshticulum.published-sdk-file-samples.v1':
            verify_sdk_files(record)
            print('Verified '+str(len(record['samples']))+' distinct fresh SDK files, part/confirmation/endpoint counts and all whole-file timing arithmetic. Qualification remains open.')
        elif record['schema']=='meshticulum.lora-sf7-sf8-comparison.v1':
            verify_lora_comparison(record)
            print('Verified 6 matched LoRa samples, three per profile, their timing arithmetic, confirmations and continuous MCU debt. Qualification remains open.')
        else:
            verify(record)
            print('Verified 30 unique RF samples, separate directional distributions and 1 retained setup failure. Missing metrics remain null.')


if __name__=='__main__':main()
