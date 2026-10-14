import pytest
from harmo.physionet_selection import select_records, acquire_selected


class Fake:
    def __init__(self, pages):
        self.pages = pages; self.calls = []
    def fetch(self, url, path, limit):
        name = url.split('/1.0.0/')[-1]; self.calls.append(name)
        data = self.pages[name].encode()
        assert len(data) <= limit
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        import hashlib
        return dict(path=str(path), url=url, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


BASE = 'https://physionet.org/files/unseen/1.0.0/'


def test_index_avoids_large_recursive_enumeration(tmp_path):
    t = Fake({'': '<a href="RECORDS">index</a><a href="huge/">huge</a>',
              'RECORDS': 'huge/r1\nhuge/r2\n' + ''.join('huge/r%d\n' % i for i in range(3, 20000))})
    result = select_records(BASE, tmp_path, t, 2)
    assert result['records'] == ['huge/r1.hea', 'huge/r2.hea']
    assert t.calls == ['', 'RECORDS']
    assert result['available_records'] is None and not result['full_inventory']


def test_depth_first_stops_before_unneeded_directories(tmp_path):
    t = Fake({'': '<a href="a/">a</a><a href="z/">z</a>',
              'a/': '<a href="r.hea">r</a><a href="z.hea">z</a>'})
    result = select_records(BASE, tmp_path, t, 1)
    assert result['records'] == ['a/r.hea'] and t.calls == ['', 'a/']


@pytest.mark.parametrize('name', ['../escape', '/etc/passwd', 'x/../../escape', 'x?url=bad', 'folder/'])
def test_index_rejects_unsafe_names(tmp_path, name):
    t = Fake({'': '<a href="RECORDS">index</a>', 'RECORDS': name})
    with pytest.raises(ValueError):
        select_records(BASE, tmp_path, t, 2)


def test_actual_header_dependency_not_filename_guess(tmp_path):
    t = Fake({'a/r.hea': 'r 1 1500 2\nshared.dat 16 10(0)/uV 0 0 0 0 0 EMG\n', 'a/shared.dat': '12'})
    assets, errors = acquire_selected(dict(records=['a/r.hea'], documents=[]), BASE, tmp_path, t)
    assert not errors and len(assets) == 2
    assert t.calls == ['a/r.hea', 'a/shared.dat']


def test_page_budget_is_explicit_not_empty_success(tmp_path):
    t = Fake({'': '<a href="a/">a</a>'})
    result = select_records(BASE, tmp_path, t, 2, max_pages=1)
    assert not result['records'] and result['stopped'] == 'page_limit'
