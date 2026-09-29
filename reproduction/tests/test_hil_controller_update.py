import json

import pytest

from reproduction.tools import update_hil_controller as installer


def deployment(tmp_path,monkeypatch):
    bundle=tmp_path/'bundle';installer.prepare(bundle)
    runtime=tmp_path/'runtime';source=runtime/'source';source.mkdir(parents=True)
    old=b'previous controller';monkeypatch.setattr(installer,'OLD_SHA',installer.sha(old))
    (source/installer.NAME).write_bytes(old);(source/'unchanged.py').write_bytes(b'other')
    manifest={installer.NAME:installer.sha(old),'unchanged.py':installer.sha(b'other')}
    (runtime/'source-sha256.json').write_text(json.dumps(manifest))
    metadata=json.loads((bundle/'bundle.json').read_text());metadata['previous_sha256']=installer.OLD_SHA
    (bundle/'bundle.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(installer.subprocess,'check_output',lambda *a,**k:'')
    return runtime,bundle,old,manifest


def test_controller_update_dry_run_backup_and_idempotence(tmp_path,monkeypatch):
    runtime,bundle,old,manifest=deployment(tmp_path,monkeypatch)
    assert installer.update(runtime,bundle)['applied'] is False
    assert (runtime/'source'/installer.NAME).read_bytes()==old
    result=installer.update(runtime,bundle,apply=True)
    assert result['applied'] and not result['robot_io']
    assert (runtime/'source'/installer.NAME).read_bytes()==(bundle/installer.NAME).read_bytes()
    assert (runtime/'source/unchanged.py').read_bytes()==b'other'
    from pathlib import Path
    assert (Path(result['backup'])/installer.NAME).read_bytes()==old
    updated=json.loads((runtime/'source-sha256.json').read_text())
    assert updated['unchanged.py']==manifest['unchanged.py'] and updated[installer.NAME]==result['sha256']
    assert installer.update(runtime,bundle,apply=True)['changed'] is False


@pytest.mark.parametrize('fault',['active','changed_source','bundle_corrupt'])
def test_controller_update_refuses_active_or_changed_deployment(tmp_path,monkeypatch,fault):
    runtime,bundle,old,manifest=deployment(tmp_path,monkeypatch)
    if fault=='active':
        def docker(argv,**kwargs):
            return 'id' if argv[1]=='ps' else json.dumps([dict(Mounts=[dict(Source=str(runtime/'source'))])])
        monkeypatch.setattr(installer.subprocess,'check_output',docker)
    if fault=='changed_source':(runtime/'source/unchanged.py').write_bytes(b'changed')
    if fault=='bundle_corrupt':(bundle/installer.NAME).write_bytes(b'bad')
    with pytest.raises(ValueError):installer.update(runtime,bundle,apply=True)
    assert (runtime/'source'/installer.NAME).read_bytes()==old
    assert json.loads((runtime/'source-sha256.json').read_text())==manifest
