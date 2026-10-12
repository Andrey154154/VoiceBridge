"""Build real PE32+ Windows executables with clang/lld on Linux or Windows.
No claim of live Windows testing is made by a successful cross-build.
"""
from pathlib import Path
import subprocess
import shutil
import argparse
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
KERNEL=['GetModuleFileNameW','GetSystemDirectoryW','GetTempPathW','GetTempFileNameW',
'DeleteFileW','CreateDirectoryW','RemoveDirectoryW','CreateFileW','WriteFile','CloseHandle',
'WaitForSingleObject','GetExitCodeProcess','GetPrivateProfileStringW','SetEnvironmentVariableW',
'GetCommandLineW','ExitProcess','CreateProcessW']
def build(output:Path,payload:Path|None=None):
    output.mkdir(parents=True,exist_ok=True)
    clang=shutil.which('clang');lld=shutil.which('lld-link')
    if not clang or not lld:raise SystemExit('LLVM clang and lld-link are required')
    for dll,names in [('kernel32',KERNEL),('user32',['MessageBoxW'])]:
        definition=output/(dll+'.def')
        definition.write_text(f'LIBRARY {dll}.dll\nEXPORTS\n'+'\n'.join(names)+'\n')
        subprocess.run([lld,'/lib','/machine:x64',f'/def:{definition}',f'/out:{output/(dll+".lib")}'],check=True)
    args=[]
    name='VoiceBridge'
    if payload:
        name='VoiceBridge-Setup-0.5.4-x64'
        def array(name,data):return f'static const unsigned char {name}[]={{'+','.join(str(b) for b in data)+'};\n'
        (output/'payload.h').write_text(array('setup_payload',payload.read_bytes())+
                                      array('setup_script',(HERE/'setup.ps1').read_bytes()))
        args=['-DSETUP',f'-I{output}']
    from resources import write
    resource=output/(name+".res")
    write(resource,HERE/"VoiceBridge.ico",bool(payload))
    obj=output/(name+'.obj');exe=output/(name+'.exe')
    subprocess.run([clang,'--target=x86_64-pc-windows-msvc','-Os','-ffreestanding','-fno-stack-protector',
       '-fno-builtin','-mno-stack-arg-probe','-Werror',*args,'-c',str(HERE/'native.c'),'-o',str(obj)],check=True)
    subprocess.run([lld,'/machine:x64','/subsystem:windows','/entry:entry','/nodefaultlib',
       '/dynamicbase','/nxcompat','/highentropyva','/timestamp:0','/manifest:embed',
       '/manifestuac:level="asInvoker" uiAccess="false"',str(obj),str(resource),str(output/'kernel32.lib'),
       str(output/'user32.lib'),f'/out:{exe}'],check=True)
    # lld embeds UAC attribute text verbatim: missing quotes produce invalid XML.
    binary = exe.read_bytes()
    start = binary.find(b'<?xml')
    end = binary.find(b'</assembly>', start)
    if start < 0 or end < 0:
        raise RuntimeError('Embedded application manifest missing')
    manifest = ET.fromstring(binary[start:end + len(b'</assembly>')])
    level = next((node for node in manifest.iter()
                  if node.tag.endswith('requestedExecutionLevel')), None)
    if level is None or level.attrib != {'level': 'asInvoker', 'uiAccess': 'false'}:
        raise RuntimeError('Incorrect application execution level')
    return exe
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--payload',type=Path)
    a=p.parse_args();print(build(a.out,a.payload))
