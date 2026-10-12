"""No network/GPU: file safety, interrupted transfer and publication contracts."""
from __future__ import annotations
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import threading
from urllib.error import HTTPError, URLError
import pytest
from voice_bridge.models import (CATALOG, ModelDownloader, ModelError, DownloadCancelled,
    ModelFile, parse_manifest, validate_local_model, _HTTPSRedirect, human_size)

REPO=CATALOG[0].repo
SHA='a'*40


def weights():
    h=json.dumps({'weight':{'dtype':'F32','shape':[1],'data_offsets':[0,4]}}).encode()
    return struct.pack('<Q',len(h))+h+b'\0'*4


def payloads():
    return {'config.json':json.dumps({'model_type':'qwen3_asr','audio_config':{},'text_config':{}}).encode(),
            'tokenizer_config.json':b'{}', 'processor_config.json':b'{}',
            'tokenizer.json':b'{"model":{}}', 'model.safetensors':weights()}


def metadata(files=None):
    files=files or payloads(); siblings=[]
    for name,data in files.items():
        entry={'rfilename':name,'size':len(data)}
        if name.endswith('.safetensors'):
            entry['lfs']={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        else:
            entry['blobId']=hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
        siblings.append(entry)
    return {'id':REPO,'sha':SHA,'siblings':siblings}


class Response(BytesIO):
    def __init__(self,data,status=200,headers=None):
        super().__init__(data); self.status=status; self.headers=headers or {}


class Server:
    def __init__(self, files=None, ignore_range=False, fail_once=False, wrong_range=False):
        self.files=files or payloads(); self.requests=[]; self.ignore_range=ignore_range
        self.fail_once=fail_once; self.wrong_range=wrong_range
    def open(self,request,timeout):
        self.requests.append(request)
        assert request.full_url.startswith('https://')
        assert timeout==10
        if '/api/' in request.full_url:
            return Response(json.dumps(metadata(self.files)).encode())
        assert f'/resolve/{SHA}/' in request.full_url
        name=request.full_url.split('/')[-1].split('?')[0]
        data=self.files[name]; offset=0
        if self.fail_once:
            self.fail_once=False; raise URLError('simulated disconnect')
        if request.get_header('Range') and not self.ignore_range:
            offset=int(request.get_header('Range')[6:-1])
            return Response(data[offset:],206,{'Content-Range':f'bytes {offset+int(self.wrong_range)}-{len(data)-1}/{len(data)}'})
        return Response(data)


def seed_model(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    for name,data in payloads().items(): (tmp_path/name).write_bytes(data)
    return tmp_path


def seed_partial(tmp_path,name='model.safetensors',partial_size=15):
    stage=tmp_path/'.downloads'/CATALOG[0].folder; stage.mkdir(parents=True)
    (stage/'.voicebridge-manifest.json').write_text(json.dumps(metadata()))
    (stage/(name+'.partial')).write_bytes(payloads()[name][:partial_size])
    return stage


def test_valid_local_model_read_only_no_network(tmp_path):
    seed_model(tmp_path)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    result=validate_local_model(tmp_path)
    assert result.path==tmp_path and result.shards==1 and not result.managed
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


@pytest.mark.parametrize('name',['config.json','processor_config.json','tokenizer.json','tokenizer_config.json','model.safetensors'])
def test_missing_file_is_rejected(tmp_path,name):
    seed_model(tmp_path); (tmp_path/name).unlink()
    with pytest.raises(ModelError): validate_local_model(tmp_path)


def test_legacy_non_native_qwen_rejected(tmp_path):
    seed_model(tmp_path); (tmp_path/'config.json').write_text('{"model_type":"qwen3_asr","thinker_config":{}}')
    with pytest.raises(ModelError,match='HF-native'): validate_local_model(tmp_path)


@pytest.mark.parametrize('data',[b'',b'version https://git-lfs.github.com/spec/v1\noid sha256:test',weights()[:-1],struct.pack('<Q',99999999)+b'{}'])
def test_truncated_or_lfs_pointer_weights_rejected(tmp_path,data):
    seed_model(tmp_path); (tmp_path/'model.safetensors').write_bytes(data)
    with pytest.raises(ModelError): validate_local_model(tmp_path)


def test_external_snapshot_symlinks_allowed(tmp_path):
    base=seed_model(tmp_path/'base'); target=tmp_path/'snapshot';target.mkdir()
    for f in base.iterdir(): (target/f.name).symlink_to(f)
    assert validate_local_model(target).shards==1


def test_sharded_model_requires_every_shard(tmp_path):
    seed_model(tmp_path); (tmp_path/'model.safetensors').rename(tmp_path/'model-00001-of-00002.safetensors')
    (tmp_path/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'a':'model-00001-of-00002.safetensors','b':'model-00002-of-00002.safetensors'}}))
    with pytest.raises(ModelError): validate_local_model(tmp_path)
    (tmp_path/'model-00002-of-00002.safetensors').write_bytes(weights())
    assert validate_local_model(tmp_path).shards==2


def test_index_cannot_read_outside_model(tmp_path):
    seed_model(tmp_path); (tmp_path/'model.safetensors.index.json').write_text('{"weight_map":{"a":"../weights.safetensors"}}')
    with pytest.raises(ModelError): validate_local_model(tmp_path)


def test_manifest_requires_repo_commit_and_digests():
    raw=metadata(); assert parse_manifest(raw,REPO).total==sum(map(len,payloads().values()))
    for field,value in [('id','evil/model'),('sha','main')]:
        other={**raw,field:value}
        with pytest.raises(ModelError): parse_manifest(other,REPO)
    raw['siblings'][0].pop('blobId')
    with pytest.raises(ModelError): parse_manifest(raw,REPO)


@pytest.mark.parametrize('name',['../escape.py','model.bin','modeling.py','/abs.safetensors','C:\\model.safetensors','subdir/model.safetensors'])
def test_manifest_filters_remote_code_pickle_and_paths(name):
    raw=metadata(); raw['siblings'].append({'rfilename':name,'size':8,'blobId':'a'*40})
    assert name not in {f.name for f in parse_manifest(raw,REPO).files}


def test_duplicate_file_rejected():
    raw=metadata(); raw['siblings'].append(raw['siblings'][0])
    with pytest.raises(ModelError): parse_manifest(raw,REPO)


def test_complete_download_atomic_published_with_hashes(tmp_path):
    events=[]; server=Server()
    result=ModelDownloader(opener=server,progress=events.append).download(REPO,tmp_path)
    assert result==tmp_path/CATALOG[0].folder
    assert validate_local_model(result).managed
    assert not (tmp_path/'.downloads'/CATALOG[0].folder).exists()
    assert all((result/n).read_bytes()==d for n,d in payloads().items())
    assert events[-1].phase=='complete'
    assert not any('Authorization' in r.headers for r in server.requests)


def test_existing_managed_reused_without_network(tmp_path):
    result=ModelDownloader(opener=Server()).download(REPO,tmp_path)
    server=Server(); assert ModelDownloader(opener=server).download(REPO,tmp_path)==result
    assert not server.requests


def test_does_not_replace_existing_unmanaged_folder(tmp_path):
    root=seed_model(tmp_path/CATALOG[0].folder)
    with pytest.raises(ModelError,match='не принадлежит'): ModelDownloader(opener=Server()).download(REPO,tmp_path)
    assert validate_local_model(root)


def test_partial_resumed_against_pinned_revision(tmp_path):
    seed_partial(tmp_path); server=Server()
    ModelDownloader(opener=server).download(REPO,tmp_path)
    req=next(r for r in server.requests if 'model.safetensors?' in r.full_url)
    assert req.get_header('Range')=='bytes=15-'
    assert all('/api/' not in r.full_url for r in server.requests)


def test_server_ignoring_range_restarts_instead_of_appending(tmp_path):
    seed_partial(tmp_path); server=Server(ignore_range=True)
    result=ModelDownloader(opener=server).download(REPO,tmp_path)
    assert (result/'model.safetensors').read_bytes()==weights()


def test_wrong_content_range_never_publishes(tmp_path):
    seed_partial(tmp_path)
    with pytest.raises(ModelError,match='диапазон'): ModelDownloader(opener=Server(wrong_range=True)).download(REPO,tmp_path)
    assert not (tmp_path/CATALOG[0].folder).exists()


def test_bad_hash_never_publishes(tmp_path):
    seed_partial(tmp_path,partial_size=0)
    server=Server(); server.files['model.safetensors']=b'x'*len(weights())
    with pytest.raises(ModelError,match='сумма'): ModelDownloader(opener=server).download(REPO,tmp_path)
    assert not (tmp_path/CATALOG[0].folder).exists()


def test_cancel_keeps_partial_and_old_selected_model_untouched(tmp_path):
    stage=seed_partial(tmp_path); cancel=threading.Event(); cancel.set()
    with pytest.raises(DownloadCancelled): ModelDownloader(opener=Server(),cancel=cancel).download(REPO,tmp_path)
    assert (stage/'model.safetensors.partial').is_file()
    assert not (tmp_path/CATALOG[0].folder).exists()


def test_cancel_mid_download_keeps_received_part(tmp_path):
    cancel=threading.Event(); server=Server(); events=[]
    def progress(event):
        events.append(event)
        if event.phase=='downloading': cancel.set()
    with pytest.raises(DownloadCancelled): ModelDownloader(opener=server,cancel=cancel,progress=progress).download(REPO,tmp_path)
    assert list((tmp_path/'.downloads'/CATALOG[0].folder).glob('*.partial'))
    assert not (tmp_path/CATALOG[0].folder).exists()


def test_low_disk_space_stops_before_weight_download(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('voice_bridge.models.shutil.disk_usage',lambda _:SimpleNamespace(free=1))
    server=Server()
    with pytest.raises(ModelError,match='места'): ModelDownloader(opener=server).download(REPO,tmp_path)
    assert len(server.requests)==1


def test_managed_stage_symlink_rejected(tmp_path):
    dest=tmp_path/'external';dest.mkdir(); cache=tmp_path/'.downloads';cache.mkdir()
    (cache/CATALOG[0].folder).symlink_to(dest,target_is_directory=True)
    with pytest.raises(ModelError): ModelDownloader(opener=Server()).download(REPO,tmp_path)
    assert not list(dest.iterdir())


def test_corrupted_managed_model_not_silently_reused(tmp_path):
    result=ModelDownloader(opener=Server()).download(REPO,tmp_path)
    (result/'model.safetensors').write_bytes(b'x'*len(weights()))
    with pytest.raises(ModelError,match='повреждена'): ModelDownloader(opener=Server()).download(REPO,tmp_path)


def test_http_error_no_infinite_retry(tmp_path):
    class Fail(Server):
        def open(self,request,timeout):
            if '/resolve/' in request.full_url:
                raise HTTPError(request.full_url,403,'blocked',{},None)
            return super().open(request,timeout)
    with pytest.raises(ModelError): ModelDownloader(opener=Fail()).download(REPO,tmp_path)


def test_no_http_redirect_downgrade():
    with pytest.raises(ModelError): _HTTPSRedirect().redirect_request(None,None,302,'',{},'http://example.com/model')


def test_model_paths_stable_not_versioned(tmp_path,monkeypatch):
    import voice_bridge.models as m
    monkeypatch.setattr(m,'data_dir',lambda:tmp_path/'data')
    assert m.default_model_dir()==tmp_path/'data'/'models'
    assert 'versions' not in str(m.default_model_dir())


def test_module_import_no_torch_qt_or_network():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c','import sys; import voice_bridge.models; assert "torch" not in sys.modules; assert "PySide6" not in sys.modules'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_human_size():
    assert human_size(1024**3)=='1.0 ГиБ'
    assert human_size(12)=='12 Б'


def test_downloader_rejects_disposable_install_location(tmp_path, monkeypatch):
    app=tmp_path/'program'; models=app/'models'
    monkeypatch.setattr('voice_bridge.models.default_model_dir',lambda:models)
    server=Server()
    for path in (app,app/'versions'/'0.5.0'/'runtime',app/'custom'):
        with pytest.raises(ModelError,match='models'):
            ModelDownloader(opener=server).download(REPO,path)
        assert not path.exists()
    assert not server.requests


def test_downloader_accepts_stable_program_model_directory(tmp_path, monkeypatch):
    models=tmp_path/'program'/'models'
    monkeypatch.setattr('voice_bridge.models.default_model_dir',lambda:models)
    result=ModelDownloader(opener=Server()).download(REPO,models)
    assert result.parent==models and validate_local_model(result).managed
