import pre_commit_flux.check_flux_helm_values as testm
import mock
import pytest


def test_basic_usecase(capsys):
    testargs = ['', 'tests/default/release.yaml']
    with mock.patch('sys.argv', testargs):
        testm.main()
        out, err = capsys.readouterr()
        assert err == ''

def test_kustomization_detect(capsys):
    testargs = ['', 'tests/kustomization/release.yaml']
    with mock.patch('sys.argv', testargs):
        testm.main()
        out, err = capsys.readouterr()
        assert 'kustomization' in out

def test_invalid_kustomizatoion():
    testargs = ['', 'tests/invalid_kustomization/release.yaml']
    with mock.patch('sys.argv', testargs):
        with pytest.raises(SystemExit) as e:
            testm.main()
        assert e.value.code == 1
