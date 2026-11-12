"""Root-reviewed non-RF devtmpfs lifecycle checks; no device node is opened."""
import argparse,ctypes,json,os,shutil,stat,tempfile,time
from pathlib import Path
from meshticulum.bench.open_watch import OpenCollisionWatch

def main():
 p=argparse.ArgumentParser();p.add_argument('--expires-epoch-us',type=int,required=True);a=p.parse_args()
 assert os.geteuid()==0
 start=time.monotonic_ns()
 def live():
  assert time.monotonic_ns()-start<10_000_000_000
  assert time.time_ns()//1000+1_000_000<a.expires_epoch_us
 live();assert time.time_ns()//1000+15_000_000<a.expires_epoch_us, 'insufficient_cleanup_reserve'
 root=Path(tempfile.mkdtemp(prefix='meshticulum-bench-selftest-',dir='/dev'))
 original=root.stat();assert original.st_uid==0 and stat.S_IMODE(original.st_mode)==0o700
 results=[];watch=None
 try:
  print(json.dumps(dict(event='private_tree_created',path=str(root),device=original.st_dev,
    inode=original.st_ino,uid=original.st_uid,mode=stat.S_IMODE(original.st_mode),pid=os.getpid(),
    original_expires_epoch_us=a.expires_epoch_us)),flush=True)
  for action in ('create_delete','rename','rename_in','rename_out','rename_exchange','replace','symlink','hardlink','chmod','mkdir','rmdir','watched_dir_move'):
   live();case=root/action;case.mkdir(mode=0o700);f=case/'file';f.write_bytes(b'x');sub=case/'dir';sub.mkdir()
   outside=root/(action+'-outside');outside.write_bytes(b'y')
   second=case/'second';second.write_bytes(b'z')
   watch=OpenCollisionWatch(f.stat(),device_root=case,opener_scan=lambda info:[],event_complete_inventory=True)
   assert case in watch.event_directories, 'actual_mount_not_event_complete'
   assert watch.current()
   if action=='create_delete':(case/'new').touch();(case/'new').unlink()
   elif action=='rename':f.rename(case/'renamed')
   elif action=='rename_in':outside.rename(case/'incoming')
   elif action=='rename_out':f.rename(root/(action+'-outgoing'))
   elif action=='rename_exchange':
    libc=ctypes.CDLL(None,use_errno=True);exchange=libc.renameat2
    exchange.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];exchange.restype=ctypes.c_int
    assert exchange(-100,os.fsencode(f),-100,os.fsencode(second),2)==0, 'rename_exchange_failed'
   elif action=='watched_dir_move':sub.rename(case/'moved-dir')
   elif action=='replace':f.unlink();f.write_bytes(b'y')
   elif action=='symlink':(case/'link').symlink_to('file')
   elif action=='hardlink':os.link(f,case/'link')
   elif action=='chmod':f.chmod(0o600)
   elif action=='mkdir':(case/'newdir').mkdir()
   else:sub.rmdir()
   assert not watch.current() and not watch.current()
   results.append({'case':action,'refused':True,'reason':watch.failed})
   watch.close();watch=None;live()
 finally:
  if watch is not None:watch.close()
  current=root.lstat();assert (current.st_dev,current.st_ino,current.st_uid)==(original.st_dev,original.st_ino,0)
  shutil.rmtree(root)
 print(json.dumps(dict(complete=True,cases=results,cleanup_complete=not root.exists(),
   rf_transmissions=0,radio_commands=0,reset_commands=0,elapsed_us=(time.monotonic_ns()-start)//1000)))
if __name__=='__main__':main()
