"""Actual Bash control flow/files/locks; apt, sudo, pip, Qt and devices are test stubs.

The test copy changes only installation paths and root guard to stay inside tmp_path.
The shipped installer has no test environment switch or path override.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


def sandbox(tmp_path,old='0.4.0'):
    folder=tmp_path/'bundle';folder.mkdir()
    app=tmp_path/'app';(app/'runtime/bin').mkdir(parents=True)
    tools=tmp_path/'tools';tools.mkdir()
    home=tmp_path/'home';home.mkdir()
    state=tmp_path/'installed-version';state.write_text(old)
    log=tmp_path/'commands.txt'
    script=(ROOT/'packaging/install.sh').read_text()
    script=script.replace('APP=/opt/voice-bridge',f'APP="{app}"')
    script=script.replace('[[ $EUID -ne 0 ]] ||', '[[ 1 == 1 ]] ||')
    (folder/'install.sh').write_text(script)
    (folder/'stop_old.py').write_text('print("TEST: process stop")\n')
    for version in ('0.3.3','0.4.0','0.5.5','0.5.6'):
        dest=folder if version=='0.5.6' else folder/'previous';dest.mkdir(exist_ok=True)
        for name in (f'VoiceBridge-Ubuntu-{version}-amd64.deb',f'voice_input_bridge-{version}-py3-none-any.whl'):
            (dest/name).write_text('test payload '+version)
    (folder/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(folder))+'\n' for p in sorted(folder.rglob('*')) if p.is_file()))
    def executable(path,code):
        path.write_text('#!'+sys.executable+'\n'+code);path.chmod(0o755)
    executable(tools/'dpkg',"import sys\nprint('amd64')\n")
    executable(tools/'dpkg-query',"import os,sys\nfrom pathlib import Path\nassert sys.argv[-1]=='voice-bridge'\nprint(Path(os.environ['VB_TEST_STATE']).read_text())\n")
    executable(tools/'sudo','''import sys,os,re
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['VB_TEST_LOG'],'a') as f:f.write(' '.join(a)+'\\n')
if a[0]=='apt-get' and 'remove' in a:
    assert a==['apt-get','remove','-y','voice-bridge']
    Path(os.environ['VB_TEST_STATE']).write_text('')
elif a[0] in ('apt-get','dpkg'):
    if a[0]=='dpkg' and os.environ.get('VB_FAIL_ROLLBACK'): sys.exit(8)
    version=re.search(r'Ubuntu-([0-9]+[.][0-9]+[.][0-9]+)',a[-1])[1]
    Path(os.environ['VB_TEST_STATE']).write_text(version)
elif a[0].endswith('/bootstrap-runtime.sh'):
    marker=Path(a[0]).parent/'runtime/bootstrap.pending'
    marker.unlink(missing_ok=True)
''')
    executable(app/'runtime/bin/python','''import os,sys
from pathlib import Path
args=sys.argv[1:]
if args and args[0].endswith('/preflight.py'):
    sys.exit(9 if os.environ.get('VB_FAIL_PREFLIGHT') else 0)
env=dict(os.environ);env['PYTHONPATH']=os.environ['VB_SOURCE']
os.execve(sys.executable,[sys.executable]+args,env)
''')
    env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_DATA_HOME=str(home/'data'),
             PATH=str(tools)+os.pathsep+os.environ['PATH'],VB_TEST_STATE=str(state),VB_TEST_LOG=str(log),
             VB_SOURCE=str(ROOT/'src'))
    cfg=home/'config/voice-bridge/config.toml';cfg.parent.mkdir(parents=True)
    cfg.write_text("# exact user profile\n[asr]\nmodel='/tmp/example-user/existing-model'\n[text]\nvocabulary_file=''\n")
    return folder,app,env,cfg,state,log


def run(folder,env,*args):
    return subprocess.run(['bash',str(folder/'install.sh'),*args],env=env,capture_output=True,text=True,timeout=20)


def test_existing_upgrade_keeps_config_and_no_asr_download(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path)
    before=cfg.read_bytes();result=run(folder,env)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'DONE: VoiceBridge 0.5.6' in result.stdout and state.read_text()=='0.5.6'
    assert cfg.read_bytes()==before
    commands=log.read_text()
    assert '--no-index --no-deps --force-reinstall' in commands
    assert 'bootstrap-runtime' not in commands and 'torch' not in commands
    pointer=Path(env['XDG_DATA_HOME'])/'voice-bridge/rollback-0.5.6.path'
    backup=Path(pointer.read_text().strip())
    assert (backup/'config.toml').read_bytes()==before
    assert (backup/'previous-version').read_text().strip()=='0.4.0'


def test_failed_preflight_restores_previous_package_and_profile(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path);env['VB_FAIL_PREFLIGHT']='1'
    before=cfg.read_bytes();result=run(folder,env)
    assert result.returncode!=0 and 'DONE:' not in result.stdout
    assert 'восстановлено' in result.stderr
    assert state.read_text()=='0.4.0' and cfg.read_bytes()==before
    assert 'voice_input_bridge-0.4.0' in log.read_text()


def test_failed_rollback_is_not_reported_as_restored(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path)
    env.update(VB_FAIL_PREFLIGHT='1',VB_FAIL_ROLLBACK='1')
    result=run(folder,env)
    assert result.returncode!=0 and 'DONE:' not in result.stdout
    assert 'ОШИБКА ОТКАТА' in result.stderr and 'восстановлено' not in result.stderr


def test_checksum_failure_stops_before_sudo(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path)
    (folder/'voice_input_bridge-0.5.6-py3-none-any.whl').write_text('changed')
    result=run(folder,env)
    assert result.returncode!=0 and not log.exists() and state.read_text()=='0.4.0'


def test_retry_resumes_explicit_incomplete_bootstrap(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='0.5.6')
    (app/'runtime/bootstrap.pending').write_text('diagnostics')
    result=run(folder,env)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'bootstrap-runtime.sh diagnostics' in log.read_text()
    assert not (app/'runtime/bootstrap.pending').exists()


def test_unknown_installed_version_is_not_overwritten(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='0.9.0')
    result=run(folder,env)
    assert result.returncode!=0 and not log.exists() and state.read_text()=='0.9.0'
