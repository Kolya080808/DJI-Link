"""Local research APK: preserve DJI code/resources; replace AppGuard bootstrap.

Original APK is never overwritten. Restored DEX checksums are verified.
"""
from pathlib import Path
import hashlib
import struct as st
import zipfile
import zlib

root=Path(__file__).resolve().parents[2]
work=Path(__file__).resolve().parent
src=root/'.git/lfs/objects/26/64/26649c10483a090ca184fefe70b3694e5cdb72b538718d502dc1419e706e0a03'

def replace_manifest(data):
 chunks=[];pos=8; strings=[];skip=0; removed=0
 while pos<len(data):
  kind,header,size=st.unpack_from('<HHI',data,pos)
  chunk=data[pos:pos+size];pos+=size
  if kind==1:
   count,stylecount,flags,start,styles=st.unpack_from('<IIIII',chunk,8)
   assert stylecount==0, 'Unexpected styled XML string pool'
   offsets=st.unpack_from('<'+'I'*count,chunk,header)
   def length8(off):
    x=chunk[off];return (((x&127)<<8)|chunk[off+1],off+2) if x&128 else (x,off+1)
   def length16(off):
    x=st.unpack_from('<H',chunk,off)[0]
    return (((x&32767)<<16)|st.unpack_from('<H',chunk,off+2)[0],off+4) if x&32768 else (x,off+2)
   for off in offsets:
    off+=start
    if flags&256:
     _,off=length8(off);n,off=length8(off);s=chunk[off:off+n].decode('utf-8')
    else:
     n,off=length16(off);s=chunk[off:off+n*2].decode('utf-16le')
    strings.append(s)
   replacements={'com.AppGuard.AppGuard.IRFNF':'com.dji.component.application.DJIApplication',
                 'com.AppGuard.AppGuard.EFYDU':'android.app.AppComponentFactory'}
   assert set(replacements).issubset(set(strings))
   original_strings=list(strings)
   strings=[replacements.get(s,s) for s in strings]
   # Re-encode as UTF-16, keeping all string indices/resource-map references.
   offsets=[];pool=bytearray()
   for s in strings:
    offsets.append(len(pool));raw=s.encode('utf-16le');n=len(raw)//2
    assert n<32768
    pool+=st.pack('<H',n)+raw+b'\0\0'
   pool+=b'\0'*((-len(pool))%4)
   start=28+count*4;size=start+len(pool)
   chunk=st.pack('<HHIIIIII',1,28,size,count,0,flags&~(256|1),start,0)+st.pack('<'+'I'*count,*offsets)+pool
  elif kind==0x102:
   ns,name=st.unpack_from('<II',chunk,16)
   attrstart,attrsize,attrcount=st.unpack_from('<HHH',chunk,24)
   guarded=False
   for i in range(attrcount):
    at=16+attrstart+i*attrsize
    attrname=st.unpack_from('<I',chunk,at+4)[0]
    typ=chunk[at+15];val=st.unpack_from('<I',chunk,at+16)[0]
    if strings[name]=='provider' and strings[attrname]=='name' and typ==3 and original_strings[val]=='com.AppGuard.AppGuard.UMMBD':guarded=True
   if skip or guarded:
    skip+=1
    if guarded:removed+=1
    continue
  elif kind==0x103 and skip:
   skip-=1;continue
  elif skip:continue
  chunks.append(chunk)
 assert removed==1 and skip==0
 body=b''.join(chunks)
 return st.pack('<HHI',3,8,len(body)+8)+body

out=work/'DJI-Fly-research-unsigned.apk'
with zipfile.ZipFile(src) as zin,zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as zout:
 for entry in zin.infolist():
  name=entry.filename
  if name.endswith('.dex') and '/' not in name:continue
  if name.startswith('META-INF/') and name.rsplit('.',1)[-1].upper() in ('RSA','DSA','EC','SF','MF'):continue
  data=zin.read(entry)
  if name=='AndroidManifest.xml':data=replace_manifest(data)
  zout.writestr(name,data)
 for i,p in enumerate(sorted((root/'dji_link_beta/reverse_docs/unpacked_app_dex').glob('*.dex')),1):
  data=p.read_bytes()
  assert data[12:32]==hashlib.sha1(data[32:]).digest()
  assert st.unpack_from('<I',data,8)[0]==zlib.adler32(data[12:])
  name='classes.dex' if i==1 else f'classes{i}.dex'
  zout.writestr(name,data)
  print(name,p.name,flush=True)
print(out, out.stat().st_size,flush=True)
