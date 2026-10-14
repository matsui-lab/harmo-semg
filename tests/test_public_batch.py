from pathlib import Path
import json
import pytest
from harmo.public_batch import normalize_url, safe_relative, inspect_header, inventory, catalog


@pytest.mark.parametrize("url",["http://physionet.org/content/emgdb/1.0.0/","https://127.0.0.1/content/a/1.0.0/",
    "https://physionet.org.evil.com/content/a/1.0.0/","https://user:pass@physionet.org/content/a/1.0.0/",
    "https://physionet.org/content/emgdb/","https://physionet.org/content/emgdb/1.0.0/../../"])
def test_url_restrictions(url):
    with pytest.raises(ValueError): normalize_url(url)


def test_version_preserved():
    assert normalize_url("https://physionet.org/files/semg/1.0.1/")[:2] == ("semg","1.0.1")


@pytest.mark.parametrize("path",["../a","/a","a/../../b","a\\b", ""])
def test_path_restrictions(path):
    with pytest.raises(ValueError): safe_relative(path)


@pytest.mark.parametrize("signal",["x.dat 16 0/mV 16 0 0 0 0 EMG","x.dat 16 200 16 0 0 0 0 EMG",
    "../x.dat 16 200/mV 16 0 0 0 0 EMG","x.dat 16+4 200/mV 16 0 0 0 0 EMG"])
def test_no_header_defaults(tmp_path,signal):
    p = tmp_path/"x.hea"
    p.write_text("x 1 1000 3\n"+signal)
    with pytest.raises(ValueError): inspect_header(p,tmp_path)


def test_header(tmp_path):
    p=tmp_path/"x.hea"
    p.write_text("x 1 1000 3\nx.dat 16 200(2)/uV 16 0 0 0 0 EMG\n")
    h=inspect_header(p,tmp_path)
    assert h["signals"][0]["baseline"]==2
    assert h["fs"]==1000


def test_inventory_never_leaves_root(tmp_path):
    class Fake:
        def fetch(self,url,dest,limit):
            dest.write_text('<a href="../">up</a><a href="a.hea">a</a><a href="https://bad.test/">bad</a>')
    assert inventory("https://physionet.org/files/a/1.0.0/",tmp_path,Fake())==["a.hea"]


def test_catalog_schema_is_repeatable():
    import sqlite3
    c=sqlite3.connect(":memory:")
    catalog(c); catalog(c)
    assert c.execute("SELECT COUNT(*) FROM records").fetchone()[0]==0


def test_calibration_accepts_only_roundoff_with_exact_adc_reconstruction():
    import numpy as np
    from harmo.public_batch import verify_calibration
    digital=np.array([-30000,-1,0,1,20000])
    gain=13789.724; baseline=-132
    values=(digital-baseline)*(1/gain)
    result=verify_calibration(values,digital,baseline,gain)
    assert result['original_integer_codes_reconstructed']
    assert result['max_error_ADC_steps'] < 1e-8
    for delta in (1/gain,0.1/gain,0.001):
        with pytest.raises(ValueError):verify_calibration(values+delta,digital,baseline,gain)
    with pytest.raises(ValueError):verify_calibration(values*1000,digital,baseline,gain)


def test_calibration_never_accepts_nan_as_matching():
    import numpy as np
    from harmo.public_batch import verify_calibration
    with pytest.raises(ValueError):verify_calibration(np.array([np.nan]),np.array([0]),0,1)
