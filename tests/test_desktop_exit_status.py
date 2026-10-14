def test_successful_self_test_has_success_exit_status(monkeypatch):
    from harmo import desktop
    calls = []
    def diagnostic(report, project):
        calls.append((report, project))
        return {'status': 'passed'}
    monkeypatch.setattr(desktop, 'self_test', diagnostic)
    assert desktop.main(['--self-test', 'check.json']) == 0
    assert calls == [('check.json', None)]
