"""Write the documented Win32 .res container for icon/version resources."""
from pathlib import Path
import struct as S

def pad(data):return data+b'\0'*((-len(data))%4)
def record(kind,name,data):
    return S.pack('<IIHHHHIHHII',len(data),32,0xffff,kind,0xffff,name,0,0x1030,0x409,0,0)+pad(data)
def block(key,value=b'',children=b'',text=True):
    head=S.pack('<HHH',0,len(value)//2 if text else len(value),int(text))
    body=pad(head+key.encode('utf-16-le')+b'\0\0')+value
    if children:body=pad(body)+children
    return S.pack('<H',len(body))+body[2:]
def write(path:Path,icon:Path,setup=False):
    data=S.pack('<IIHHHHIHHII',0,32,0xffff,0,0xffff,0,0,0,0,0,0)
    raw=icon.read_bytes();n=S.unpack_from('<H',raw,4)[0]
    group=S.pack('<HHH',0,1,n)
    for i in range(n):
        w,h,c,z,planes,bits,size,offset=S.unpack_from('<BBBBHHII',raw,6+16*i)
        data+=record(3,101+i,raw[offset:offset+size])
        group+=S.pack('<BBBBHHIH',w,h,c,z,planes,bits,size,101+i)
    data+=record(14,1,group)
    strings={'CompanyName':'Voice Bridge','FileDescription':'Voice Bridge online setup' if setup else 'Voice Bridge desktop',
             'FileVersion':'0.5.4.0','ProductVersion':'0.5.4.0','ProductName':'Voice Bridge',
             'OriginalFilename':'VoiceBridge-Setup-0.5.4-x64.exe' if setup else 'VoiceBridge.exe'}
    fields=b''.join(pad(block(k,(v+'\0').encode('utf-16-le'))) for k,v in strings.items())
    info=block('StringFileInfo',children=block('040904B0',children=fields))
    var=block('VarFileInfo',children=block('Translation',S.pack('<HH',0x409,1200),text=False))
    fixed=S.pack('<13I',0xfeef04bd,0x10000,5,0x40000,5,0x40000,0x3f,0,0x40004,1,0,0,0)
    version=block('VS_VERSION_INFO',fixed,pad(info)+var,text=False)
    data+=record(16,1,version)
    path.write_bytes(data)
