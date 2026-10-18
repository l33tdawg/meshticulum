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
    require(record['schema']=='meshticulum.lora-sf7-sf8-comparison.v2','unsupported LoRa report')
    require(re.fullmatch('[0-9a-f]{40}',record['source_commit']) is not None,'invalid LoRa source pin')
    require(re.fullmatch('[0-9a-f]{64}',record['sdk_sha256']) is not None,'invalid LoRa SDK hash')
    require(record['complete'] is True,'incomplete LoRa report')
    require(record['matched_pairs']==6 and len(record['samples'])==12,'wrong LoRa denominator')
    require(len(record['campaigns'])==2,'wrong campaign count')
    campaign_ids={row['campaign_id'] for row in record['campaigns']}
    require(campaign_ids=={'final-main-20261018','first-1024-20261018'},'wrong campaign ids')
    payloads={row['sha256']:row for row in record['payloads']}
    require(len(record['payloads'])==6 and len(payloads)==6,'wrong payload count')
    require(all(row['bytes']==8164 and re.fullmatch('[0-9a-f]{64}',row['sha256']) for row in record['payloads']),'invalid payload')
    for campaign in record['campaigns']:
        rows=[row for row in record['samples'] if row['campaign']==campaign['campaign_id']]
        require(len(rows)==6,'wrong campaign sample count')
        for profile in ('sf7-500','sf8-500'):
            group=[row for row in rows if row['profile']==profile]
            require(len(group)==3 and sorted(row['repeat'] for row in group)==[0,1,2],'wrong profile repeats')
        for repeat in range(3):
            pair=[row for row in rows if row['repeat']==repeat]
            require(len({row['payload_sha256'] for row in pair})==1,'unmatched payload pair')
        require(campaign['receiver_attempts']==sum(row['receiver']['attempt_journal']['attempts'] for row in rows),'campaign receiver attempt mismatch')
        require(campaign['sender_attempts']==sum(row['sender']['attempt_journal']['attempts'] for row in rows),'campaign sender attempt mismatch')

    for row in record['samples']:
        require(row['profile'] in ('sf7-500','sf8-500'),'unknown LoRa profile')
        require(row['payload_bytes']==8164 and row['payload_sha256'] in payloads,'invalid LoRa payload')
        require(row['radio_manifest_sha256']==row['payload_sha256'],'LoRa manifest/payload mismatch')
        expected_sf=7 if row['profile']=='sf7-500' else 8
        for side in ('receiver','sender'):
            profile=row[side]['profile']
            require(profile['frequency_hz']==921375000 and profile['bandwidth_hz']==500000
                    and profile['spreading_factor']==expected_sf and profile['coding_rate']==5
                    and profile['chip_power_dbm']==0 and profile['preamble']==8
                    and profile['sync_word']==0x42 and profile['lease_ms']==90000
                    and profile['max_boot_attempts']==1024
                    and profile['max_boot_estimated_airtime_us']==60000000,
                    'wrong LoRa profile')
            journal=row[side]['attempt_journal']
            require(type(journal['attempts']) is int and journal['attempts']>0,'invalid LoRa attempts')
            require(type(journal['reserved_estimated_airtime_us']) is int
                    and journal['reserved_estimated_airtime_us']>0,'invalid LoRa airtime')
            require(journal['fenced'] is False and journal['recovered_unknown']==0,'unsettled LoRa attempt')
            timing=row[side]['timing']
            require(timing['armed_to_complete_us']>0,'invalid LoRa interval')
            require(abs(timing['armed_to_complete_us']
                        -(timing['complete_epoch_us']-timing['armed_epoch_us']))<=1,
                    'LoRa receipt interval mismatch')
        require(row['receiver']['receiver_chunk_verified'] is True,'LoRa chunk not verified')
        require(row['receiver']['whole_file_verified'] is False
                and row['sender']['whole_file_verified'] is False,'LoRa scope inflated')
        require(row['receiver']['receipt_confirmation']['peer_receipt_confirmed'] is True,'missing receiver receipt')
        require(row['sender']['sender_peer_receipt'] is True,'missing sender peer receipt')
        require(row['sender']['receipt_confirmation']['attempted'] is True
                and row['sender']['receipt_confirmation']['outcome']=='RF_DONE',
                'missing sender confirmation')
        receiver_timing=row['receiver']['timing']
        require(abs(receiver_timing['armed_to_receiver_verified_us']
                    -(receiver_timing['receiver_verified_monotonic_us']
                      -receiver_timing['armed_monotonic_us']))<=1,
                'receiver verified interval mismatch')

    confirmation=record['confirmation']
    require(confirmation['campaigns_completed']==2 and confirmation['samples']==12
            and confirmation['matched_pairs']==6,'wrong confirmation denominator')
    require(confirmation['receiver_chunks_verified']==12
            and confirmation['authenticated_receipt_confirmations']==12
            and confirmation['sender_peer_receipts']==12,'missing LoRa completion')
    require(confirmation['fenced_attempts']==0 and confirmation['recovered_unknown_attempts']==0,'LoRa debt unsettled')
    receiver_attempts=sum(row['receiver']['attempt_journal']['attempts'] for row in record['samples'])
    sender_attempts=sum(row['sender']['attempt_journal']['attempts'] for row in record['samples'])
    require(receiver_attempts==confirmation['receiver_attempts']==172,'wrong receiver attempt total')
    require(sender_attempts==confirmation['sender_attempts']==528,'wrong sender attempt total')
    require(confirmation['mcu_attempt_delta']==receiver_attempts,'MCU attempt delta mismatch')
    require(confirmation['mcu_attempts_before']==208 and confirmation['mcu_attempts_after']==380,'wrong MCU attempts')
    require(confirmation['mcu_reserved_estimated_airtime_delta_us']
            ==confirmation['mcu_reserved_estimated_airtime_after_us']
              -confirmation['mcu_reserved_estimated_airtime_before_us'],
            'MCU airtime delta mismatch')
    require(record['operation_policy']['boot_attempts_cap']==1024,'wrong LoRa attempt cap')
    require(record['operation_policy']['boot_estimated_airtime_cap_us']==60000000,'wrong LoRa airtime cap')
    require(record['operation_policy']['attempts_remaining_after_campaigns']==644,'wrong LoRa attempt headroom')
    require(record['operation_policy']['estimated_airtime_remaining_after_campaigns_us']==43280256,'wrong LoRa airtime headroom')
    require(record['scope']['whole_file_assembly'] is False
            and record['scope']['completion_distribution'] is False
            and record['scope']['field_range_measured'] is False
            and record['scope']['rf_airtime_at_antenna_measured'] is False
            and record['scope']['energy_measured'] is False
            and record['scope']['eirp_measured'] is False
            and record['scope']['upstream_ranking'] is False,'LoRa scope inflated')
    require(bool(record['limitations']),'missing LoRa limitations')

    def measurements(rows):
        result={}
        for profile in ('sf7-500','sf8-500'):
            group=[row for row in rows if row['profile']==profile]
            sender=statistics.median(row['sender']['timing']['armed_to_complete_us']/1000000 for row in group)
            receiver=statistics.median(row['receiver']['timing']['armed_to_receiver_verified_us']/1000000 for row in group)
            startup=statistics.median(
                (row['sender']['timing']['complete_epoch_us']-row['sender']['timing']['started_epoch_us'])/1000000
                for row in group)
            result[profile]={'sender':sender,'receiver':receiver,'startup':startup}
        return result
    groups={
        'combined':record['samples'],
        'final':[row for row in record['samples'] if row['campaign']=='final-main-20261018'],
        'control':[row for row in record['samples'] if row['campaign']=='first-1024-20261018'],
    }
    for name,rows in groups.items():
        expected=measurements(rows)
        published=record['measurements'][name]
        for profile,key in (('sf7-500','sf7'),('sf8-500','sf8')):
            row=published[key]
            require(math.isclose(row['sender_arm_to_receipt_median_s'],expected[profile]['sender'],rel_tol=1e-9),'wrong '+name+' SF median')
            require(math.isclose(row['useful_Bps'],8164/expected[profile]['sender'],rel_tol=1e-9),'wrong '+name+' rate')
            require(math.isclose(row['receiver_arm_to_verified_median_s'],expected[profile]['receiver'],rel_tol=1e-9),'wrong '+name+' receiver median')
            require(math.isclose(row['startup_to_receipt_median_s'],expected[profile]['startup'],rel_tol=1e-9),'wrong '+name+' startup median')
        require(math.isclose(published['sf8_to_sf7_time_ratio'],
                             expected['sf8-500']['sender']/expected['sf7-500']['sender'],
                             rel_tol=1e-9),'wrong '+name+' ratio')


def verify_http_gateway(record):
    require(record['schema']=='meshticulum.http-radio-gateway-browser-runs.v1','unsupported HTTP report')
    require(record['complete'] is True,'incomplete HTTP report')
    require(re.fullmatch('[0-9a-f]{40}',record['source_commit']) is not None,'invalid HTTP source pin')
    for field in ('sdk_sha256','gateway_script_sha256'):
        require(re.fullmatch('[0-9a-f]{64}',record[field]) is not None,'invalid '+field)
    require(record['browser']=='links' and record['receive_timeout_seconds']==30,'wrong browser scope')
    require(record['startup_gap_seconds']==3.0,'wrong startup gap')
    require(record['pre_fix_control']['thirty_second']['returncode']==1
            and record['pre_fix_control']['thirty_second']['elapsed_ms']>30000,
            'pre-fix control was not a 30-second failure')
    require(record['pre_fix_control']['ninety_second']['returncode']==0,'pre-fix 90-second control failed')
    one=record['one_shot_control']
    require(one['status']==200 and one['http_body_bytes']==577
            and one['complete_seconds']<30,'one-shot control missed 30-second window')
    require(one['http_body_sha256']=='25ddf2c883e0d1958ea971d279a7e4f0fd446724ee3db7db19dadabd4a62e484',
            'wrong one-shot body hash')
    require(len(record['runs'])==3,'wrong browser sample count')
    seen=set()
    for row in record['runs']:
        require(row['run_id'] not in seen,'duplicate browser run');seen.add(row['run_id'])
        require(row['url'] in ('http://example.com/','http://example.org/'),'unexpected HTTP target')
        require(row['status']==200 and row['http_body_bytes']==577,'browser HTTP result incomplete')
        require(row['http_body_sha256']==one['http_body_sha256'],'browser body hash mismatch')
        require(0<row['gateway_complete_seconds']<row['browser_elapsed_seconds']<=30,
                'browser run missed the 30-second window')
        require(row['gateway_complete_seconds']<20,'gateway completion outside measured bound')
        for attempts in (row['request_radio_attempts'],row['response_radio_attempts']):
            require(len(attempts)==2 and all(type(value) is int and value>0 for value in attempts),
                    'invalid HTTP radio attempt counts')
        for field in ('request_radio_complete_s','internet_fetch_complete_s','response_staged_s'):
            require(type(row[field]) in (int,float) and 0<row[field]<row['gateway_complete_seconds'],
                    'invalid HTTP timing '+field)
    interactive=record['interactive_browsing']
    require(interactive['client']=='links','wrong interactive HTTP client')
    for key in ('search','followed_result'):
        row=interactive[key]
        require(row['url'].startswith('http://'),'interactive browsing not plain HTTP')
        require(0<row['body_bytes']<=16384 and re.fullmatch('[0-9a-f]{64}',row['body_sha256']),
                'invalid interactive body')
        require(0<row['gateway_complete_seconds']<30 and row['browser_wall_seconds']<30,
                'interactive browsing missed 30-second window')
        require(0<row['response_over_radio_bytes']<=16384,'interactive response exceeds gateway cap')
        for attempts in (row['request_radio_attempts'],row['response_radio_attempts']):
            require(len(attempts)==2 and all(type(value) is int and value>0 for value in attempts),
                    'invalid interactive attempt counts')
    limits=interactive['limits']
    require(limits['gateway_max_body_bytes']==16384,'wrong interactive body cap')
    require(limits['google_search_body_bytes']>limits['gateway_max_body_bytes'],
            'Google search body unexpectedly within cap')
    require(limits['murgasarc_body_bytes']>limits['gateway_max_body_bytes'],
            'large result unexpectedly within cap')
    require('HTTPS' in limits['google_root'] and limits['https_connect'] is False,
            'interactive HTTPS boundary missing')
    require(limits['links_installed_on_unit_a'] is False,'wrong links placement')
    require(record['all_body_hashes_match'] is True and record['all_radio_directions_verified'] is True,
            'HTTP verification missing')
    scope=record['scope']
    for field in ('transparent_ip_routing','tun_interface','https_connect','latency_distribution',
                  'field_range_measured','rf_airtime_at_antenna_measured','energy_measured',
                  'eirp_measured'):
        require(scope[field] is False,'HTTP scope inflated: '+field)
    require(scope['one_request_per_browser_run'] is True and scope['plain_http_only'] is True,
            'HTTP scope boundary missing')
    require(bool(record['limitations']),'HTTP limitations missing')
    for field,value in record['evidence_sha256'].items():
        require(re.fullmatch('[0-9a-f]{64}',value) is not None,'invalid evidence hash '+field)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',nargs='?',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=[args.report] if args.report else [root/'benchmarks/2026-10-09-radio/radio-results.json',
                                           root/'benchmarks/2026-10-17-public-sdk/results.json',
                                           root/'provenance/PUBLIC_INSTALLED_SDK_PROOF.json']
    for path in paths:
        record=json.loads(path.read_text())
        if record['schema']=='meshticulum.published-sdk-file-samples.v1':
            verify_sdk_files(record)
            print('Verified '+str(len(record['samples']))+' distinct fresh SDK files, part/confirmation/endpoint counts and all whole-file timing arithmetic. Qualification remains open.')
        elif record['schema']=='meshticulum.lora-sf7-sf8-comparison.v2':
            verify_lora_comparison(record)
            print('Verified 12 LoRa chunks, six matched pairs, both campaign medians, the 1,024-attempt cap and the 644-attempt remaining headroom. Field-range and whole-file claims remain absent.')
        elif record['schema']=='meshticulum.http-radio-gateway-browser-runs.v1':
            verify_http_gateway(record)
            print('Verified three HTTP browser runs inside the 30-second timeout, matching body hashes and both radio directions. Transparent-IP, field-range, energy and EIRP claims remain absent.')
        else:
            verify(record)
            print('Verified 30 unique RF samples, separate directional distributions and 1 retained setup failure. Missing metrics remain null.')


if __name__=='__main__':main()
